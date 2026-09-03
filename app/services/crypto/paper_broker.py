"""Pure paper-only order lifecycle with deterministic fills and audit records."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    strategy_id: str
    symbol: str
    side: str
    quantity: float
    submitted_at_utc: str
    eligible_execution_bar_utc: str
    status: str = "accepted"


def create_order(*, strategy_id: str, symbol: str, side: str, quantity: float, eligible_execution_bar_utc: str) -> PaperOrder:
    if side not in {"buy", "sell", "short", "cover"} or quantity <= 0:
        raise ValueError("invalid paper order")
    return PaperOrder(str(uuid4()), strategy_id, symbol.upper(), side, float(quantity), now_iso(), eligible_execution_bar_utc)


def fill_order(order: PaperOrder, raw_price: float, *, fee_rate: float, slippage: float, filled_at_utc: str) -> dict:
    if order.status != "accepted" or raw_price <= 0 or fee_rate < 0 or slippage < 0:
        raise ValueError("order cannot be filled")
    direction = 1 if order.side in {"buy", "cover"} else -1
    fill_price = raw_price * (1 + direction * slippage)
    notional = fill_price * order.quantity
    return {"fill_id": str(uuid4()), "order": asdict(order), "raw_price": raw_price, "fill_price": fill_price, "fee": notional * fee_rate, "slippage": abs(fill_price - raw_price) * order.quantity, "filled_at_utc": filled_at_utc, "status": "filled", "paper_only": True}
