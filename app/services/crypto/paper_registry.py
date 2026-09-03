"""Deterministic registry and risk gate for paper-only strategy execution."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from typing import Literal


StrategyState = Literal["challenger", "paper_champion", "suspended", "retired"]


def canonical_sha256(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass(frozen=True)
class StrategyRegistryEntry:
    strategy_id: str
    candidate_id: str
    state: StrategyState
    allowed_regimes: tuple[str, ...]
    symbols: tuple[str, ...]
    timeframe: str
    parameters_sha256: str
    effective_from_utc: str
    risk_budget_pct: float
    max_symbol_exposure_pct: float
    limitations: tuple[str, ...] = ("paper_only",)

    def __post_init__(self) -> None:
        if self.state not in {"challenger", "paper_champion", "suspended", "retired"}:
            raise ValueError("invalid strategy state")
        if not 0 < self.risk_budget_pct <= 1 or not 0 < self.max_symbol_exposure_pct <= 1:
            raise ValueError("risk budgets must be within (0, 1]")
        if self.max_symbol_exposure_pct > self.risk_budget_pct:
            raise ValueError("symbol exposure cannot exceed strategy budget")

    def export_dict(self) -> dict:
        payload = asdict(self)
        payload["allowed_regimes"] = list(self.allowed_regimes)
        payload["symbols"] = list(self.symbols)
        payload["limitations"] = list(self.limitations)
        payload["artifact_sha256"] = canonical_sha256(payload)
        return payload


@dataclass(frozen=True)
class RiskSnapshot:
    data_ready: bool
    total_exposure_pct: float
    strategy_exposure_pct: float
    symbol_exposure_pct: float
    daily_loss_pct: float
    consecutive_losses: int


@dataclass(frozen=True)
class RiskPolicy:
    max_total_exposure_pct: float
    max_daily_loss_pct: float
    max_consecutive_losses: int


def authorize_paper_order(entry: StrategyRegistryEntry, regime: str, snapshot: RiskSnapshot, policy: RiskPolicy) -> tuple[bool, str]:
    if entry.state != "paper_champion":
        return False, "strategy_not_paper_champion"
    if regime not in entry.allowed_regimes:
        return False, "regime_not_allowed"
    if not snapshot.data_ready:
        return False, "market_data_not_ready"
    if snapshot.total_exposure_pct >= policy.max_total_exposure_pct:
        return False, "total_exposure_limit"
    if snapshot.strategy_exposure_pct >= entry.risk_budget_pct:
        return False, "strategy_exposure_limit"
    if snapshot.symbol_exposure_pct >= entry.max_symbol_exposure_pct:
        return False, "symbol_exposure_limit"
    if snapshot.daily_loss_pct <= -policy.max_daily_loss_pct:
        return False, "daily_loss_circuit_breaker"
    if snapshot.consecutive_losses >= policy.max_consecutive_losses:
        return False, "consecutive_loss_cooldown"
    return True, "accepted"
