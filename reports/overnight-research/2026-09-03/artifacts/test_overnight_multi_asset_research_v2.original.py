import subprocess
import sys
from pathlib import Path

import pandas as pd

from research.crypto_backtest import run_overnight_multi_asset_research_v2 as overnight_cli
from research.crypto_backtest.overnight_multi_asset_research_v2 import (
    PORTFOLIO_EXPOSURE_PCT,
    SYMBOL_EXPOSURE_PCT,
    STAGE_INDEX,
    STAGES,
    build_parameter_budget,
    can_open_position,
    holdout_classification,
    run_signal_family,
    utc_now,
)


def test_parameter_budget_is_frozen_and_below_caps():
    budget = build_parameter_budget()
    assert {family: len(rows) for family, rows in budget.items()} == {"A": 24, "B": 32, "C": 24, "D": 16}
    assert sum(len(rows) for rows in budget.values()) == 96


def test_portfolio_cap_blocks_ninth_ten_percent_position():
    equity = 1000.0
    notional = equity * SYMBOL_EXPOSURE_PCT
    assert PORTFOLIO_EXPOSURE_PCT == 0.80
    assert can_open_position(equity, current_exposure=700.0, proposed_notional=notional)
    assert not can_open_position(equity, current_exposure=800.0, proposed_notional=notional)


def test_holdout_classification_requires_cost_stress_and_trade_count():
    metrics = {
        "trade_count": 301,
        "win_rate": 52.0,
        "net_expectancy_bps": 0.8,
        "profit_factor": 1.12,
        "max_drawdown_pct": -12.0,
    }
    stress = {"net_expectancy_bps": -0.1, "profit_factor": 0.99}
    status, reasons = holdout_classification(metrics, stress)
    assert status == "rejected_holdout"
    assert reasons == ["stress_expectancy_le_0", "stress_profit_factor_le_1.00"]


def test_mean_reversion_run_uses_1000u_and_t_plus_1_open(monkeypatch):
    timeline = pd.date_range("2026-06-01 00:00:00+00:00", periods=5, freq="15min", tz="UTC")
    monkeypatch.setitem(STAGES, "development", (str(timeline[0]), str(timeline[-1])))
    monkeypatch.setitem(STAGE_INDEX, "development", timeline)

    view = pd.DataFrame(
        {
            "open": [100.0, 99.0, 101.0, 101.0, 101.0],
            "high": [100.5, 99.5, 101.5, 101.5, 101.5],
            "low": [99.5, 98.5, 100.5, 100.5, 100.5],
            "close": [98.0, 101.0, 101.0, 101.0, 101.0],
            "atr14": [1.0, 1.0, 1.0, 1.0, 1.0],
            "bb_mid_20": [100.0, 100.0, 100.0, 100.0, 100.0],
            "bb_std_20": [0.5, 0.5, 0.5, 0.5, 0.5],
            "rsi_7": [10.0, 50.0, 50.0, 50.0, 50.0],
            "mark_close": [98.0, 101.0, 101.0, 101.0, 101.0],
            "mark_open": [100.0, 99.0, 101.0, 101.0, 101.0],
        },
        index=timeline,
    )
    stage_views = {"AAA": {"development": view}}
    params = {
        "family": "A",
        "param_id": "A20",
        "bb_window": 20,
        "bb_width": 1.8,
        "rsi_length": 7,
        "rsi_entry": 20,
        "max_hold_bars": 12,
    }
    result = run_signal_family("A", params, "development", [{"symbol": "AAA", "tier": "hot"}], stage_views)
    assert len(result["closed_trades"]) == 1
    trade = result["closed_trades"][0]
    assert trade["entry_timestamp_utc"] == str(timeline[1])
    assert trade["exit_timestamp_utc"] == str(timeline[2])
    assert trade["notional"] == 100.0


def test_cli_script_help_runs_from_repo_root():
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "research" / "crypto_backtest" / "run_overnight_multi_asset_research_v2.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "--manifest" in completed.stdout
    assert "No module named 'research'" not in completed.stderr


def test_write_running_summary_marks_report_running(tmp_path):
    summary_path = tmp_path / "morning-summary.md"
    output_dir = tmp_path / "out"
    manifest = Path("reports/crypto-backtest/strategy-discovery-sprint-v1/dataset-repair-v1-final2/normalized_data_manifest.json")
    universe = Path("reports/crypto-backtest/strategy-discovery-sprint-v1/universe_manifest.json")
    overnight_cli.write_running_summary(summary_path, output_dir, manifest, universe)
    content = summary_path.read_text(encoding="utf-8")
    assert "- 状态：running" in content
    assert str(output_dir) in content
    assert str(manifest) in content


def test_utc_now_uses_shanghai_offset():
    assert "+08:00" in utc_now()
