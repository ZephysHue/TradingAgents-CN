"""Auditable, paper-only strategy runner.

This service is deliberately unable to call an exchange order endpoint.  A
registry row must already be promoted by a human to ``paper_champion`` before
an entry can be created; exits are allowed to reduce an existing paper
position.  Every decision is persisted as an audit event.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Protocol

import pandas as pd

from app.services.crypto.bybit_client import BybitFuturesClient
from app.services.crypto.market_context import classify_regime
from app.services.crypto.paper_broker import create_order, fill_order
from app.services.crypto.paper_registry import (
    RiskPolicy,
    RiskSnapshot,
    StrategyRegistryEntry,
    authorize_paper_order,
)


REGISTRY = "strategy_paper_registry"
POSITIONS = "strategy_paper_positions"
ORDERS = "strategy_paper_orders"
FILLS = "strategy_paper_fills"
AUDIT = "strategy_paper_audit_events"
RUNTIME = "strategy_paper_runtime_state"


class KlineProvider(Protocol):
    async def klines(self, symbol: str, interval: str, limit: int) -> list[dict[str, Any]]: ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _strategy_signal(spec: dict[str, Any], close: pd.Series) -> tuple[bool, bool]:
    """Return entry/exit decisions on a fully closed signal series."""
    if len(close) < 3:
        return False, False
    kind = spec.get("kind", "sma_cross")
    fast = int(spec.get("fast_window", 20))
    slow = int(spec.get("slow_window", 60))
    signal_window = int(spec.get("signal_window", 14))
    lower = float(spec.get("lower_threshold", 30.0))
    upper = float(spec.get("upper_threshold", 70.0))
    alpha = float(spec.get("band_alpha", 2.0))
    if kind == "buy_and_hold":
        return True, False
    if kind == "sma_cross":
        left, right = close.rolling(fast).mean(), close.rolling(slow).mean()
        return bool(left.iloc[-1] > right.iloc[-1] and left.iloc[-2] <= right.iloc[-2]), bool(left.iloc[-1] < right.iloc[-1] and left.iloc[-2] >= right.iloc[-2])
    if kind == "rsi_reversion":
        delta = close.diff()
        gain, loss = delta.clip(lower=0).rolling(signal_window).mean(), (-delta.clip(upper=0)).rolling(signal_window).mean()
        rsi = 100 - 100 / (1 + gain / loss.replace(0, pd.NA))
        return bool(rsi.iloc[-1] <= lower < rsi.iloc[-2]), bool(rsi.iloc[-1] >= upper > rsi.iloc[-2])
    if kind == "macd_cross":
        macd = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
        line = macd.ewm(span=signal_window, adjust=False).mean()
        return bool(macd.iloc[-1] > line.iloc[-1] and macd.iloc[-2] <= line.iloc[-2]), bool(macd.iloc[-1] < line.iloc[-1] and macd.iloc[-2] >= line.iloc[-2])
    if kind == "bollinger_reversion":
        mean, std = close.rolling(slow).mean(), close.rolling(slow).std(ddof=0)
        lower_band = mean - alpha * std
        return bool(close.iloc[-1] >= lower_band.iloc[-1] and close.iloc[-2] < lower_band.iloc[-2]), bool(close.iloc[-1] >= mean.iloc[-1] and close.iloc[-2] < mean.iloc[-2])
    raise ValueError(f"unsupported StrategySpec kind: {kind}")


class PaperAutomationService:
    """Mongo-backed orchestration with injectable read-only market data."""

    def __init__(self, db: Any, provider: KlineProvider | None = None, *, fee_rate: float = 0.0004, slippage: float = 0.0002, initial_equity: float = 100_000.0) -> None:
        self.db, self.provider = db, provider or BybitFuturesClient()
        self.fee_rate, self.slippage, self.initial_equity = fee_rate, slippage, initial_equity
        if min(fee_rate, slippage) < 0 or initial_equity <= 0:
            raise ValueError("invalid paper execution assumptions")

    async def ensure_indexes(self) -> None:
        await self.db[REGISTRY].create_index("strategy_id", unique=True)
        await self.db[POSITIONS].create_index([("strategy_id", 1), ("symbol", 1)], unique=True)
        await self.db[ORDERS].create_index("order_id", unique=True)
        await self.db[FILLS].create_index("fill_id", unique=True)
        await self.db[AUDIT].create_index("event_at_utc")
        await self.db[RUNTIME].create_index([("strategy_id", 1), ("symbol", 1)], unique=True)

    async def upsert_registry(self, entry: StrategyRegistryEntry, strategy_spec: dict[str, Any]) -> None:
        document = entry.export_dict() | {"strategy_spec": strategy_spec, "updated_at_utc": utc_now().isoformat()}
        await self.db[REGISTRY].replace_one({"strategy_id": entry.strategy_id}, document, upsert=True)

    async def _audit(self, event_type: str, payload: dict[str, Any]) -> None:
        await self.db[AUDIT].insert_one({"event_type": event_type, "event_at_utc": utc_now().isoformat(), "paper_only": True, **payload})

    async def _positions(self) -> list[dict[str, Any]]:
        return [doc async for doc in self.db[POSITIONS].find({"quantity": {"$gt": 0}})]

    async def run_once(self) -> dict[str, int]:
        """Process only new, fully closed bars; return counts, never place live orders."""
        champions = [doc async for doc in self.db[REGISTRY].find({"state": "paper_champion"})]
        result = {"champions": len(champions), "processed": 0, "orders": 0, "skipped": 0}
        if not champions:
            await self._audit("no_active_paper_champion", {})
            return result
        positions = await self._positions()
        total_exposure = sum(float(item.get("notional", 0)) / self.initial_equity for item in positions)
        for document in champions:
            entry = StrategyRegistryEntry(**{key: tuple(value) if key in {"allowed_regimes", "symbols", "limitations"} else value for key, value in document.items() if key in StrategyRegistryEntry.__dataclass_fields__})
            spec = document.get("strategy_spec", {})
            for symbol in entry.symbols:
                bars = await self.provider.klines(symbol, entry.timeframe, 300)
                closed = [bar for bar in bars if _utc(bar["close_time"]) < utc_now()]
                if len(closed) < 30:
                    result["skipped"] += 1
                    await self._audit("market_data_not_ready", {"strategy_id": entry.strategy_id, "symbol": symbol, "bars": len(closed)})
                    continue
                execution_bar, signal_bars = closed[-1], closed[:-1]
                state = await self.db[RUNTIME].find_one({"strategy_id": entry.strategy_id, "symbol": symbol})
                if state and state.get("last_execution_bar_utc") == execution_bar["open_time"]:
                    result["skipped"] += 1
                    continue
                close = pd.Series([bar["close"] for bar in signal_bars], dtype=float)
                context = classify_regime(close)
                entry_signal, exit_signal = _strategy_signal(spec, close)
                position = await self.db[POSITIONS].find_one({"strategy_id": entry.strategy_id, "symbol": symbol})
                has_position = bool(position and float(position.get("quantity", 0)) > 0)
                decision = "no_signal"
                if has_position and exit_signal:
                    await self._execute(entry, symbol, "sell", float(position["quantity"]), execution_bar, context, position)
                    result["orders"] += 1
                    decision = "exit_filled"
                elif not has_position and entry_signal:
                    symbol_exposure = 0.0
                    snapshot = RiskSnapshot(True, total_exposure, 0.0, symbol_exposure, 0.0, 0)
                    allowed, reason = authorize_paper_order(entry, context["deterministic_label"], snapshot, RiskPolicy(0.8, 0.03, 3))
                    if allowed:
                        quantity = self.initial_equity * entry.max_symbol_exposure_pct / float(execution_bar["open"])
                        await self._execute(entry, symbol, "buy", quantity, execution_bar, context, None)
                        result["orders"] += 1
                        total_exposure += entry.max_symbol_exposure_pct
                        decision = "entry_filled"
                    else:
                        decision = reason
                await self.db[RUNTIME].replace_one(
                    {"strategy_id": entry.strategy_id, "symbol": symbol},
                    {"strategy_id": entry.strategy_id, "symbol": symbol, "last_execution_bar_utc": execution_bar["open_time"], "last_context": context}, upsert=True,
                )
                await self._audit("bar_decision", {"strategy_id": entry.strategy_id, "symbol": symbol, "execution_bar_utc": execution_bar["open_time"], "context": context, "decision": decision})
                result["processed"] += 1
        return result

    async def _execute(self, entry: StrategyRegistryEntry, symbol: str, side: str, quantity: float, bar: dict[str, Any], context: dict[str, Any], position: dict[str, Any] | None) -> None:
        order = create_order(strategy_id=entry.strategy_id, symbol=symbol, side=side, quantity=quantity, eligible_execution_bar_utc=bar["open_time"])
        fill = fill_order(order, float(bar["open"]), fee_rate=self.fee_rate, slippage=self.slippage, filled_at_utc=bar["open_time"])
        await self.db[ORDERS].insert_one(asdict(order) | {"paper_only": True, "context": context})
        await self.db[FILLS].insert_one(fill | {"context": context})
        next_quantity = 0.0 if side == "sell" else quantity
        next_notional = 0.0 if side == "sell" else float(fill["fill_price"]) * quantity
        await self.db[POSITIONS].replace_one(
            {"strategy_id": entry.strategy_id, "symbol": symbol},
            {"strategy_id": entry.strategy_id, "symbol": symbol, "quantity": next_quantity, "notional": next_notional, "last_fill_id": fill["fill_id"], "updated_at_utc": bar["open_time"], "paper_only": True}, upsert=True,
        )
