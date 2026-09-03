import pytest

from app.services.crypto.paper_registry import RiskPolicy, RiskSnapshot, StrategyRegistryEntry, authorize_paper_order


def champion(**overrides):
    values = dict(strategy_id="s1", candidate_id="c1", state="paper_champion", allowed_regimes=("range",), symbols=("BTCUSDT",), timeframe="15m", parameters_sha256="sha256:params", effective_from_utc="2026-09-02T00:00:00Z", risk_budget_pct=0.2, max_symbol_exposure_pct=0.1)
    values.update(overrides)
    return StrategyRegistryEntry(**values)


def snapshot(**overrides):
    values = dict(data_ready=True, total_exposure_pct=0.2, strategy_exposure_pct=0.0, symbol_exposure_pct=0.0, daily_loss_pct=0.0, consecutive_losses=0)
    values.update(overrides)
    return RiskSnapshot(**values)


def policy():
    return RiskPolicy(max_total_exposure_pct=0.8, max_daily_loss_pct=0.03, max_consecutive_losses=3)


def test_only_champion_in_matching_regime_can_trade():
    assert authorize_paper_order(champion(), "range", snapshot(), policy()) == (True, "accepted")
    assert authorize_paper_order(champion(state="challenger"), "range", snapshot(), policy()) == (False, "strategy_not_paper_champion")
    assert authorize_paper_order(champion(), "trend_up", snapshot(), policy()) == (False, "regime_not_allowed")


@pytest.mark.parametrize("snapshot_kwargs,reason", [
    ({"data_ready": False}, "market_data_not_ready"),
    ({"total_exposure_pct": 0.8}, "total_exposure_limit"),
    ({"strategy_exposure_pct": 0.2}, "strategy_exposure_limit"),
    ({"symbol_exposure_pct": 0.1}, "symbol_exposure_limit"),
    ({"daily_loss_pct": -0.03}, "daily_loss_circuit_breaker"),
    ({"consecutive_losses": 3}, "consecutive_loss_cooldown"),
])
def test_risk_gates_block_orders(snapshot_kwargs, reason):
    assert authorize_paper_order(champion(), "range", snapshot(**snapshot_kwargs), policy()) == (False, reason)


def test_registry_rejects_symbol_limit_above_strategy_budget():
    with pytest.raises(ValueError, match="symbol exposure"):
        champion(risk_budget_pct=0.1, max_symbol_exposure_pct=0.2)
