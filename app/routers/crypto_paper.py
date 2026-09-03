"""Crypto isolated-margin paper trading endpoints."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_mongo_db
from app.core.response import ok
from app.routers.auth_db import get_current_user
from app.services.crypto import BinanceFuturesClient, BinanceMarketDataError
from app.services.crypto.paper_leverage import (
    DEFAULT_FEE_RATE, DEFAULT_INITIAL_BALANCE, DEFAULT_MAINTENANCE_MARGIN_RATE,
    build_position, close_position, mark_position,
)

router = APIRouter(prefix="/crypto/paper", tags=["crypto-paper"])
market_client = BinanceFuturesClient()
user_locks: dict[str, asyncio.Lock] = {}


class OpenRequest(BaseModel):
    symbol: str = Field(..., examples=["BTCUSDT"])
    side: Literal["long", "short"]
    quantity: float = Field(..., gt=0)
    leverage: int = Field(5, ge=1, le=50)
    price: float | None = Field(None, gt=0, description="不传则读取 Binance 标记价格")


class CloseRequest(BaseModel):
    symbol: str
    price: float | None = Field(None, gt=0, description="不传则读取 Binance 标记价格")


class MarkRequest(BaseModel):
    symbol: str
    price: float | None = Field(None, gt=0, description="不传则读取 Binance 标记价格")


def _lock(user_id: str) -> asyncio.Lock:
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


async def _account(db, user_id: str) -> dict[str, Any]:
    account = await db.crypto_paper_accounts.find_one({"user_id": user_id}, {"_id": 0})
    if account:
        return account
    account = {
        "user_id": user_id, "asset": "USDT", "initial_balance": DEFAULT_INITIAL_BALANCE,
        "wallet_balance": DEFAULT_INITIAL_BALANCE, "realized_pnl": 0.0,
        "fees_paid": 0.0, "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.crypto_paper_accounts.insert_one(account)
    return {k: v for k, v in account.items() if k != "_id"}


async def _price(symbol: str, supplied: float | None) -> float:
    if supplied is not None:
        return float(supplied)
    try:
        quote = await market_client.quote(symbol)
        return float(quote["mark_price"])
    except (ValueError, BinanceMarketDataError) as exc:
        raise HTTPException(status_code=502, detail=f"无法获取 {symbol.upper()} 标记价格: {exc}") from exc


async def _summary(db, user_id: str) -> dict[str, Any]:
    account = await _account(db, user_id)
    positions = await db.crypto_paper_positions.find({"user_id": user_id, "status": "open"}, {"_id": 0}).to_list(None)
    unrealized = sum(float(p.get("unrealized_pnl", 0.0)) for p in positions)
    used_margin = sum(float(p.get("margin", 0.0)) for p in positions)
    return {
        "account": {**account, "used_margin": used_margin, "unrealized_pnl": unrealized,
                    "equity": float(account["wallet_balance"]) + used_margin + unrealized,
                    "available_balance": float(account["wallet_balance"])},
        "positions": positions,
    }


@router.get("/account")
async def account(current_user: dict = Depends(get_current_user)):
    return ok(await _summary(get_mongo_db(), str(current_user["id"])))


@router.post("/open")
async def open_position(payload: OpenRequest, current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["id"])
    async with _lock(user_id):
        db = get_mongo_db()
        symbol = market_client.normalize_symbol(payload.symbol)
        exists = await db.crypto_paper_positions.find_one({"user_id": user_id, "symbol": symbol, "status": "open"})
        if exists:
            raise HTTPException(status_code=409, detail="该交易对已有未平仓仓位，第一版不支持加仓或反向开仓")
        price = await _price(symbol, payload.price)
        position = build_position(symbol=symbol, side=payload.side, quantity=payload.quantity, entry_price=price, leverage=payload.leverage)
        account_doc = await _account(db, user_id)
        required = position["margin"] + position["open_fee"]
        if float(account_doc["wallet_balance"]) < required:
            raise HTTPException(status_code=400, detail=f"可用余额不足，需要 {required:.4f} USDT")
        position["user_id"] = user_id
        response_position = {k: v for k, v in position.items() if k != "user_id"}
        await db.crypto_paper_positions.insert_one(position)
        await db.crypto_paper_accounts.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": -required, "fees_paid": position["open_fee"]}})
        return ok({"position": response_position, "required_margin": required}, "paper position opened")


@router.post("/mark")
async def mark_position_price(payload: MarkRequest, current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["id"])
    async with _lock(user_id):
        db = get_mongo_db()
        symbol = market_client.normalize_symbol(payload.symbol)
        position = await db.crypto_paper_positions.find_one({"user_id": user_id, "symbol": symbol, "status": "open"}, {"_id": 0})
        if not position:
            raise HTTPException(status_code=404, detail="没有找到未平仓仓位")
        price = await _price(symbol, payload.price)
        marked = mark_position(position, price)
        await db.crypto_paper_positions.update_one({"user_id": user_id, "symbol": symbol, "status": "open"}, {"$set": marked})
        if marked["liquidation_triggered"]:
            closed = close_position(marked, price)
            await db.crypto_paper_positions.update_one({"user_id": user_id, "symbol": symbol, "status": "open"}, {"$set": closed})
            release = max(0.0, float(marked["margin"]) + float(marked["unrealized_pnl"]) - float(closed["close_fee"]))
            await db.crypto_paper_accounts.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": release, "fees_paid": closed["close_fee"], "realized_pnl": closed["net_pnl"]}})
            return ok({"position": closed, "liquidated": True}, "position liquidated")
        return ok({"position": marked, "liquidated": False}, "position marked")


@router.post("/close")
async def close_open_position(payload: CloseRequest, current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["id"])
    async with _lock(user_id):
        db = get_mongo_db()
        symbol = market_client.normalize_symbol(payload.symbol)
        position = await db.crypto_paper_positions.find_one({"user_id": user_id, "symbol": symbol, "status": "open"}, {"_id": 0})
        if not position:
            raise HTTPException(status_code=404, detail="没有找到未平仓仓位")
        price = await _price(symbol, payload.price)
        closed = close_position(position, price)
        await db.crypto_paper_positions.update_one({"user_id": user_id, "symbol": symbol, "status": "open"}, {"$set": closed})
        release = float(position["margin"]) + float(closed["realized_pnl"]) - float(closed["close_fee"])
        await db.crypto_paper_accounts.update_one({"user_id": user_id}, {"$inc": {"wallet_balance": release, "fees_paid": closed["close_fee"], "realized_pnl": closed["net_pnl"]}})
        await db.crypto_paper_trades.insert_one({"user_id": user_id, "symbol": symbol, "side": position["side"], "entry_price": position["entry_price"], "exit_price": price, "quantity": position["quantity"], "leverage": position["leverage"], "net_pnl": closed["net_pnl"], "closed_at": closed["closed_at"]})
        return ok({"trade": closed, "released_balance": release}, "paper position closed")


@router.post("/reset")
async def reset_account(current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["id"])
    async with _lock(user_id):
        db = get_mongo_db()
        await db.crypto_paper_positions.delete_many({"user_id": user_id})
        await db.crypto_paper_trades.delete_many({"user_id": user_id})
        await db.crypto_paper_accounts.replace_one({"user_id": user_id}, {"user_id": user_id, "asset": "USDT", "initial_balance": DEFAULT_INITIAL_BALANCE, "wallet_balance": DEFAULT_INITIAL_BALANCE, "realized_pnl": 0.0, "fees_paid": 0.0, "reset_at": datetime.now(timezone.utc).isoformat()}, upsert=True)
        return ok(await _summary(db, user_id), "paper account reset")
