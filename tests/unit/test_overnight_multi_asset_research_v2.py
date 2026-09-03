import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import research.crypto_backtest.overnight_multi_asset_research_v2 as overnight
from research.crypto_backtest.overnight_multi_asset_research_v2 import (
    MODEL_COMPLETIONS_URL,
    PORTFOLIO_EXPOSURE_PCT,
    SYMBOL_EXPOSURE_PCT,
    STAGE_INDEX,
    STAGES,
    build_parameter_budget,
    can_open_position,
    holdout_classification,
    run_signal_family,
    write_running_summary,
)
from research.crypto_backtest.run_overnight_multi_asset_research_v2 import snapshot_sources, write_verification_bundle


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
    assert completed.returncode == 0, completed.stderr
    assert "--manifest" in completed.stdout
    assert "No module named 'research'" not in completed.stderr


def test_write_running_summary_marks_report_running(tmp_path):
    summary_path = tmp_path / "morning-summary.md"
    output_dir = tmp_path / "out"
    write_running_summary(
        summary_path=summary_path,
        output_dir=output_dir,
        status="running_validation_holdout",
        started_at_utc="2026-09-03 03:14:26 UTC+08:00",
        notes=["note one", "note two"],
        chosen_rows={
            "A": {
                "family": "A",
                "param_id": "A01",
                "trade_count": 120,
                "net_expectancy_bps": 1.25,
                "profit_factor": 1.3,
            }
        },
        validation_rows=[{"family": "A", "status": "validation_pass", "trade_count": 160, "net_expectancy_bps": 0.8, "profit_factor": 1.1}],
        holdout_rows=[],
        candidate_registry=[],
        rejection_registry=[],
    )
    content = summary_path.read_text(encoding="utf-8")
    assert "- 状态：running_validation_holdout" in content
    assert str(output_dir) in content
    assert "`A01`" in content
    assert "validation_pass" in content


def test_run_overnight_research_all_validation_rejects_still_writes_outputs(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"files":[]}\n', encoding="utf-8")
    universe_path = tmp_path / "universe.json"
    universe_path.write_text('{"selected":[]}\n', encoding="utf-8")

    monkeypatch.setattr(overnight, "load_frozen_data", lambda *_: ({}, {}, {"files": []}, {"selected": []}, []))
    monkeypatch.setattr(overnight, "build_stage_views", lambda *_: {})
    monkeypatch.setattr(
        overnight,
        "stage_members",
        lambda *_: {stage: [{"symbol": f"{stage.upper()}-AAA", "tier": "hot", "rank": 1}] for stage in STAGES},
    )
    monkeypatch.setattr(
        overnight,
        "market_stage_summary",
        lambda stage, members, *_: {"stage": stage, "symbols": len(members), "tiers": {}},
    )
    monkeypatch.setattr(
        overnight,
        "build_parameter_budget",
        lambda: {
            "A": [{"family": "A", "param_id": "A01"}],
            "B": [{"family": "B", "param_id": "B01"}],
            "C": [{"family": "C", "param_id": "C01"}],
            "D": [{"family": "D", "param_id": "D01"}],
        },
    )
    monkeypatch.setattr(
        overnight,
        "_run_family",
        lambda *args, **kwargs: {"closed_trades": [], "unresolved_positions": [], "audit": {"mock": True}},
    )

    def fake_metrics(stage, family, params, *_args, **_kwargs):
        metrics = {
            "family": family,
            "family_name": overnight.FAMILY_NAMES[family],
            "param_id": params["param_id"],
            "params_json": overnight._json_dumps(params),
            "stage": stage,
            "trade_count": 180 if stage == "development" else 20,
            "win_rate": 55.0 if stage == "development" else 40.0,
            "net_expectancy_bps": 1.2 if stage == "development" else -0.5,
            "profit_factor": 1.2 if stage == "development" else 0.8,
            "net_pnl": 12.0 if stage == "development" else -2.0,
            "ending_equity": 1012.0 if stage == "development" else 998.0,
            "max_drawdown_pct": -5.0,
            "avg_holding_bars": 4.0,
            "max_holding_bars": 8.0,
            "unresolved_positions": 0,
            "terminal_mtm_pnl": 0.0,
            "max_gross_exposure_pct": 40.0,
            "drawdown_start_utc": None,
            "drawdown_trough_utc": None,
            "recovery_utc": None,
            "duration_15m_bars": 0,
        }
        curve = pd.DataFrame(
            [
                {
                    "timestamp_utc": "2026-06-01 00:00:00+00:00",
                    "equity": 1000.0,
                    "realized_component": 0.0,
                    "unrealized_component": 0.0,
                    "gross_exposure": 0.0,
                    "active_positions": 0,
                }
            ]
        )
        return metrics, curve

    monkeypatch.setattr(overnight, "metrics_for_run", fake_metrics)
    monkeypatch.setattr(overnight, "stress_metrics_from_holdout", lambda *_args, **_kwargs: pytest.fail("holdout should not run"))
    monkeypatch.setattr(
        overnight,
        "run_local_model_analysis",
        lambda *_args, **_kwargs: {
            "health_status_code": 200,
            "health_body": {"status": "ok"},
            "models_status_code": 200,
            "chat_status_code": "not_run",
        },
    )

    output_dir = tmp_path / "output"
    summary_path = output_dir / "morning-summary.md"
    result = overnight.run_overnight_research(manifest_path, universe_path, output_dir, summary_path)

    assert result["candidate_registry"] == []
    assert len(result["rejection_registry"]) == 4
    holdout_lines = (output_dir / "holdout_results.csv").read_text(encoding="utf-8").splitlines()
    stress_lines = (output_dir / "cost_stress_results.csv").read_text(encoding="utf-8").splitlines()
    assert holdout_lines[0].startswith("family,")
    assert stress_lines[0].startswith("family,")
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "状态：completed" in summary_text
    assert "exploratory challenger 数量：0" in summary_text


def test_verification_bundle_writes_required_artifacts(tmp_path):
    repo_root = tmp_path / "repo"
    for relative_path, content in (
        (Path("research/crypto_backtest/overnight_multi_asset_research_v2.py"), "executor\n"),
        (Path("research/crypto_backtest/run_overnight_multi_asset_research_v2.py"), "runner\n"),
        (Path("tests/unit/test_overnight_multi_asset_research_v2.py"), "tests\n"),
    ):
        target = repo_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    output_dir = tmp_path / "output"
    manifest_path = tmp_path / "manifest.json"
    universe_path = tmp_path / "universe.json"
    manifest_path.write_text('{"files":[]}\n', encoding="utf-8")
    universe_path.write_text('{"selected":[]}\n', encoding="utf-8")

    snapshots = snapshot_sources(output_dir, repo_root)
    pytest_result = {
        "command": "python -m pytest tests/unit/test_overnight_multi_asset_research_v2.py -q",
        "exit_code": 0,
        "result": "8 passed in 1.23s",
    }
    run_result = {
        "command": "python research/crypto_backtest/run_overnight_multi_asset_research_v2.py",
        "exit_code": 0,
        "result": '{"candidates": 0, "rejections": 4}',
    }

    write_verification_bundle(output_dir, manifest_path, universe_path, pytest_result, run_result, snapshots)

    verification_text = (output_dir / "VERIFICATION.txt").read_text(encoding="utf-8")
    assert "BASELINE_EXIT_CODE: 0" in verification_text
    assert "MODIFIED_EXIT_CODE: 0" in verification_text
    assert "ARTIFACT_LOGS: artifacts/pytest.log; artifacts/run_cli.log" in verification_text
    assert (output_dir / "ROLLBACK.sh").exists()
    assert (output_dir / "verification_artifacts" / "research" / "crypto_backtest" / "overnight_multi_asset_research_v2.py").exists()


def test_run_local_model_analysis_uses_completions_endpoint_and_cleans_reasoning_prefix(monkeypatch):
    calls: list[tuple[str, dict[str, object], int]] = []

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    def fake_get(url, timeout):
        if url == overnight.MODEL_HEALTH_URL:
            return FakeResponse(200, {"status": "ok"})
        if url == overnight.MODEL_MODELS_URL:
            return FakeResponse(200, {"data": [{"id": "local-model"}]})
        raise AssertionError(f"unexpected GET {url}")

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse(
            200,
            {
                "choices": [
                    {
                        "text": '.\n\n</think>\n\n{"overall_market_structure":"bearish","family_fit":[{"family":"A","why_it_fits":"oversold bounces","failure_mode":"win rate low"}],"audit_focus":["validation gate"]}'
                    }
                ]
            },
        )

    monkeypatch.setattr(overnight.requests, "get", fake_get)
    monkeypatch.setattr(overnight.requests, "post", fake_post)

    result = overnight.run_local_model_analysis(
        {
            "A": {
                "param_id": "A08",
                "trade_count": 689,
                "win_rate": 45.283,
                "net_expectancy_bps": 39.906,
                "profit_factor": 1.342,
                "max_drawdown_pct": -6.289,
            }
        },
        {"stage": "development", "symbols": 30, "tiers": {"hot": {"symbols": 10}}},
    )

    assert calls == [(MODEL_COMPLETIONS_URL, result["request_payload"], 90)]
    assert result["completion_status_code"] == 200
    assert result["completion_text_clean"].startswith('{"overall_market_structure":"bearish"')
    assert result["completion_json_candidate"]["audit_focus"] == ["validation gate"]
