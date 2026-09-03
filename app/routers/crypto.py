"""Authenticated crypto market-data endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database import get_mongo_db
from app.routers.auth_db import get_current_user
from app.services.crypto import BybitFuturesClient, BybitMarketDataError

router = APIRouter(prefix="/crypto", tags=["crypto"])
client = BybitFuturesClient()


def _error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))


@router.get("/bybit/status")
async def bybit_status(_: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return {"success": True, "data": await client.status()}
    except BybitMarketDataError as exc:
        raise _error(exc) from exc


@router.get("/bybit/symbols")
async def bybit_symbols(_: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return {"success": True, "data": await client.symbols()}
    except BybitMarketDataError as exc:
        raise _error(exc) from exc


@router.get("/bybit/{symbol}/quote")
async def bybit_quote(symbol: str, _: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return {"success": True, "data": await client.quote(symbol)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BybitMarketDataError as exc:
        raise _error(exc) from exc


@router.get("/bybit/{symbol}/klines")
async def bybit_klines(
    symbol: str,
    interval: str = Query("1m"),
    limit: int = Query(240, ge=10, le=1500),
    persist: bool = Query(False),
    _: dict = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        rows = await client.klines(symbol, interval, limit)
    except (ValueError, BybitMarketDataError) as exc:
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise _error(exc) from exc
    if persist:
        db = get_mongo_db()
        await db.crypto_klines.create_index([("symbol", 1), ("interval", 1), ("open_time", 1)], unique=True)
        for row in rows:
            await db.crypto_klines.update_one(
                {"symbol": row["symbol"], "interval": row["interval"], "open_time": row["open_time"]},
                {"$set": row},
                upsert=True,
            )
    return {"success": True, "data": {"symbol": symbol.upper(), "interval": interval, "count": len(rows), "persisted": persist, "items": rows}}


@router.get("/bybit/{symbol}/volatility")
async def bybit_volatility(
    symbol: str,
    interval: str = Query("1m"),
    limit: int = Query(240, ge=20, le=1500),
    _: dict = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return {"success": True, "data": await client.volatility(symbol, interval, limit)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BybitMarketDataError as exc:
        raise _error(exc) from exc


@router.get("/bybit/{symbol}/snapshot")
async def bybit_snapshot(
    symbol: str,
    interval: str = Query("1m"),
    limit: int = Query(240, ge=20, le=1500),
    _: dict = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return {"success": True, "data": await client.snapshot(symbol, interval, limit)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BybitMarketDataError as exc:
        raise _error(exc) from exc
