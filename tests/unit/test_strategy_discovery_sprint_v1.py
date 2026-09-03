import pandas as pd

from research.crypto_backtest.run_strategy_discovery_sprint_v1 import expected_index, select_universe


def test_stage_windows_are_fixed_and_non_overlapping():
    development = expected_index("2026-06-01T00:00:00Z", "2026-06-30T23:45:00Z")
    validation = expected_index("2026-07-01T00:00:00Z", "2026-07-31T23:45:00Z")
    assert len(development.intersection(validation)) == 0


def test_monthly_tiers_do_not_use_future_month_membership():
    registry = {"months": [{"month": "2026-06", "tiers": {tier: {"symbols": [f"{tier}{n}" for n in range(10)]} for tier in ("hot", "mid", "low")}}, {"month": "2026-07", "tiers": {tier: {"symbols": [f"j{tier}{n}" for n in range(10)]} for tier in ("hot", "mid", "low")}}, {"month": "2026-08", "tiers": {tier: {"symbols": [f"a{tier}{n}" for n in range(10)]} for tier in ("hot", "mid", "low")}}]}
    selected, insufficient = select_universe(registry)
    assert not insufficient
    assert {row["symbol"] for row in selected if row["stage"] == "development"} == {f"{tier}{n}" for tier in ("hot", "mid", "low") for n in range(10)}


def test_coverage_gate_is_99_percent_scale():
    assert len(expected_index("2026-08-01T00:00:00Z", "2026-08-29T23:45:00Z")) == 2784
    assert pd.Timestamp("2026-08-01T00:00:00Z").tz is not None
