"""Deterministic isolated-margin paper trading calculations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


DEFAULT_INITIAL_BALANCE = 10_000.0
DEFAULT_FEE_RATE = 0.0004
DEFAULT_MAINTENANCE_MARGIN_RATE = 0.005
MAX_LEVERAGE = 50


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive(value: float, name: str) -> float:
    value = float(value)
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return value


def build_position(
    *, symbol: str, side: str, quantity: float, entry_price: float, leverage: int,
    fee_rate: float = DEFAULT_FEE_RATE,
    maintenance_margin_rate: float = DEFAULT_MAINTENANCE_MARGIN_RATE,
) -> dict[str, Any]:
    if side not in {"long", "short"}:
        raise ValueError("side must be long or short")
    quantity = _positive(quantity, "quantity")
    entry_price = _positive(entry_price, "entry_price")
    leverage = int(leverage)
    if leverage < 1 or leverage > MAX_LEVERAGE:
        raise ValueError(f"leverage must be between 1 and {MAX_LEVERAGE}")
    notional = quantity * entry_price
    margin = notional / leverage
    open_fee = notional * fee_rate
    liquidation_price = (
        entry_price * (1 - 1 / leverage + maintenance_margin_rate)
        if side == "long"
        else entry_price * (1 + 1 / leverage - maintenance_margin_rate)
    )
    return {
        "symbol": symbol.upper(), "side": side, "quantity": quantity,
        "entry_price": entry_price, "leverage": leverage, "notional": notional,
        "margin": margin, "open_fee": open_fee, "fee_rate": fee_rate,
        "maintenance_margin_rate": maintenance_margin_rate,
        "liquidation_price": liquidation_price, "mark_price": entry_price,
        "unrealized_pnl": 0.0, "roi_pct": 0.0, "status": "open",
        "opened_at": now_iso(), "updated_at": now_iso(),
    }


def mark_position(position: dict[str, Any], mark_price: float) -> dict[str, Any]:
    mark_price = _positive(mark_price, "mark_price")
    entry = float(position["entry_price"])
    quantity = float(position["quantity"])
    sign = 1 if position["side"] == "long" else -1
    pnl = (mark_price - entry) * quantity * sign
    margin = float(position["margin"])
    liq = float(position["liquidation_price"])
    liquidated = mark_price <= liq if position["side"] == "long" else mark_price >= liq
    return {
        **position,
        "mark_price": mark_price,
        "unrealized_pnl": pnl,
        "roi_pct": pnl / margin * 100 if margin else 0.0,
        "liquidation_triggered": liquidated,
        "updated_at": now_iso(),
    }


def close_position(position: dict[str, Any], exit_price: float) -> dict[str, Any]:
    marked = mark_position(position, exit_price)
    close_notional = float(position["quantity"]) * exit_price
    close_fee = close_notional * float(position["fee_rate"])
    realized_pnl = float(marked["unrealized_pnl"])
    return {
        **marked,
        "exit_price": exit_price,
        "close_fee": close_fee,
        "realized_pnl": realized_pnl,
        "net_pnl": realized_pnl - float(position["open_fee"]) - close_fee,
        "status": "liquidated" if marked["liquidation_triggered"] else "closed",
        "closed_at": now_iso(),
    }
