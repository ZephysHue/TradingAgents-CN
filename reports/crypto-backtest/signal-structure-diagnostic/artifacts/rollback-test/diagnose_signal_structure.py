"""Fixed-signal, BTC-base audit for the corrected BTCUSD_PERP baseline."""
from __future__ import annotations

import argparse
import json
import math
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

import numpy as np
import pandas as pd

from coin_m_engine import ContractSpec, fee_btc, fill_prices, pnl_btc, risk_per_contract_btc


SPEC = ContractSpec()
TOL = 1e-12
RISK_PCT = 0.01
SCENARIOS = {
    "A_0fee_0slip": {"fee_rate": 0.0, "slippage_bp": 0.0},
    "B_004fee_0slip": {"fee_rate": 0.0004, "slippage_bp": 0.0},
    "C_0fee_2bp": {"fee_rate": 0.0, "slippage_bp": 2.0},
    "D_004fee_2bp": {"fee_rate": 0.0004, "slippage_bp": 2.0},
}


def as_decimal(value: float | int) -> Decimal:
    return Decimal(str(value))


def exit_trigger(row: pd.Series) -> float:
    return float(row.sl if row.exit_reason == "SL" else row.tp)


def quantity_for_risk(equity_btc: float, row: pd.Series, slippage_bp: float) -> tuple[float, int, float, float, float]:
    slip = as_decimal(slippage_bp / 10000)
    entry_fill, stop_fill = fill_prices(row.direction, as_decimal(row.raw_entry), as_decimal(row.sl), slip)
    risk_amount = equity_btc * RISK_PCT
    per_contract = float(risk_per_contract_btc(entry_fill, stop_fill, SPEC))
    raw = risk_amount / per_contract if per_contract > 0 else 0.0
    rounded = int((as_decimal(raw) / as_decimal(SPEC.step_size)).to_integral_value(rounding=ROUND_DOWN)) * SPEC.step_size
    rounded = min(rounded, SPEC.max_qty)
    if rounded < SPEC.min_qty:
        rounded = 0
    return raw, rounded, risk_amount, float(entry_fill), float(stop_fill)


def replay_scenario(signals: pd.DataFrame, fee_rate: float, slippage_bp: float, initial_btc: float) -> pd.DataFrame:
    equity = initial_btc
    output: list[dict] = []
    for signal_id, row in signals.iterrows():
        qty_raw, qty, risk_budget, entry_fill, stop_fill = quantity_for_risk(equity, row, slippage_bp)
        raw_exit = exit_trigger(row)
        _, actual_exit_fill = fill_prices(row.direction, as_decimal(row.raw_entry), as_decimal(raw_exit), as_decimal(slippage_bp / 10000))
        equity_before = equity
        if qty == 0:
            raw_pnl = fill_pnl = entry_fee = exit_fee = net = 0.0
            price_risk = all_in_risk = 0.0
        else:
            raw_pnl = float(pnl_btc(row.direction, qty, as_decimal(row.raw_entry), as_decimal(raw_exit), SPEC))
            fill_pnl = float(pnl_btc(row.direction, qty, as_decimal(entry_fill), actual_exit_fill, SPEC))
            entry_fee = float(fee_btc(qty, as_decimal(entry_fill), as_decimal(fee_rate), SPEC))
            exit_fee = float(fee_btc(qty, actual_exit_fill, as_decimal(fee_rate), SPEC))
            net = fill_pnl - entry_fee - exit_fee
            price_risk = abs(float(pnl_btc(row.direction, qty, as_decimal(entry_fill), as_decimal(stop_fill), SPEC)))
            stop_entry_fee = float(fee_btc(qty, as_decimal(entry_fill), as_decimal(fee_rate), SPEC))
            stop_exit_fee = float(fee_btc(qty, as_decimal(stop_fill), as_decimal(fee_rate), SPEC))
            all_in_risk = price_risk + stop_entry_fee + stop_exit_fee
        slippage_cost = raw_pnl - fill_pnl
        equity += net
        usd_mark = float(actual_exit_fill)
        output.append({
            "signal_id": int(signal_id), "entry_time": row.entry_time, "exit_time": row.exit_time,
            "direction": row.direction, "exit_reason": row.exit_reason, "raw_entry": float(row.raw_entry),
            "sl": float(row.sl), "tp": float(row.tp), "raw_exit": raw_exit,
            "entry_fill": entry_fill, "exit_fill": usd_mark, "contracts_raw": qty_raw,
            "contracts": qty, "risk_budget_btc": risk_budget, "price_risk_btc": price_risk,
            "all_in_risk_btc": all_in_risk, "gross_raw_pnl_btc": raw_pnl,
            "filled_pnl_btc": fill_pnl, "entry_fee_btc": entry_fee, "exit_fee_btc": exit_fee,
            "fee_btc": entry_fee + exit_fee, "slippage_cost_btc": slippage_cost,
            "net_pnl_btc": net, "price_r": net / price_risk if price_risk else np.nan,
            "all_in_r": net / all_in_risk if all_in_risk else np.nan,
            "equity_before_btc": equity_before, "equity_after_btc": equity,
            "equity_after_usd_mark": equity * usd_mark,
        })
    return pd.DataFrame(output)


def drawdown(series: pd.Series, initial: float) -> float:
    curve = pd.concat([pd.Series([initial]), series.reset_index(drop=True)], ignore_index=True)
    return float((curve / curve.cummax() - 1).min())


def profit_factor(values: pd.Series) -> float | None:
    positive = float(values[values > 0].sum())
    negative = float(values[values <= 0].sum())
    return positive / abs(negative) if negative else None


def summarize(frame: pd.DataFrame, initial_btc: float, initial_usd: float) -> dict:
    traded = frame[frame.contracts > 0].copy()
    gross_profit = float(traded.loc[traded.gross_raw_pnl_btc > 0, "gross_raw_pnl_btc"].sum())
    gross_loss = float(traded.loc[traded.gross_raw_pnl_btc <= 0, "gross_raw_pnl_btc"].sum())
    final_btc = float(frame.equity_after_btc.iloc[-1])
    final_usd = float(frame.equity_after_usd_mark.iloc[-1])
    return {
        "signals": int(len(frame)), "trades": int(len(traded)),
        "long_trades": int((traded.direction == "long").sum()), "short_trades": int((traded.direction == "short").sum()),
        "win_rate_net": float((traded.net_pnl_btc > 0).mean()), "win_rate_gross": float((traded.gross_raw_pnl_btc > 0).mean()),
        "gross_profit_btc": gross_profit, "gross_loss_btc": gross_loss,
        "gross_pnl_btc": float(traded.gross_raw_pnl_btc.sum()),
        "fee_btc": float(traded.fee_btc.sum()), "slippage_cost_btc": float(traded.slippage_cost_btc.sum()),
        "net_pnl_btc": float(traded.net_pnl_btc.sum()),
        "initial_equity_btc": initial_btc, "final_equity_btc": final_btc,
        "return_btc_pct": (final_btc / initial_btc - 1) * 100,
        "initial_equity_usd": initial_usd, "final_equity_usd_mark": final_usd,
        "return_usd_mark_pct": (final_usd / initial_usd - 1) * 100,
        "expectancy_price_r": float(traded.price_r.mean()), "expectancy_all_in_r": float(traded.all_in_r.mean()),
        "profit_factor": profit_factor(traded.net_pnl_btc),
        "gross_profit_factor": profit_factor(traded.gross_raw_pnl_btc),
        "max_drawdown_btc_pct": drawdown(frame.equity_after_btc, initial_btc) * 100,
        "max_drawdown_usd_mark_pct": drawdown(frame.equity_after_usd_mark, initial_usd) * 100,
        "skipped_min_qty": int((frame.contracts == 0).sum()),
    }


def assert_accounting(frame: pd.DataFrame, summary: dict) -> None:
    assert np.isclose(frame.gross_raw_pnl_btc.sum() - frame.slippage_cost_btc.sum(), frame.filled_pnl_btc.sum(), atol=TOL)
    assert np.isclose(frame.filled_pnl_btc.sum() - frame.fee_btc.sum(), frame.net_pnl_btc.sum(), atol=TOL)
    assert np.isclose(summary["initial_equity_btc"] + frame.net_pnl_btc.sum(), summary["final_equity_btc"], atol=TOL)
    assert np.isclose(frame.gross_raw_pnl_btc.sum(), summary["gross_pnl_btc"], atol=TOL)
    assert np.isclose(frame.fee_btc.sum(), summary["fee_btc"], atol=TOL)
    assert np.isclose(frame.slippage_cost_btc.sum(), summary["slippage_cost_btc"], atol=TOL)
    assert np.isclose(frame.net_pnl_btc.sum(), summary["net_pnl_btc"], atol=TOL)


def direction_summary(frames: dict[str, pd.DataFrame], initial_btc: float, initial_usd: float) -> pd.DataFrame:
    rows = []
    for scenario, frame in frames.items():
        for direction in ("long", "short"):
            part = frame[frame.direction == direction].copy()
            if part.empty:
                continue
            part["equity_after_btc"] = initial_btc + part.net_pnl_btc.cumsum()
            part["equity_after_usd_mark"] = part.equity_after_btc * part.exit_fill
            stats = summarize(part, initial_btc, initial_usd)
            rows.append({"scenario": scenario, "direction": direction,
                         "trades": stats["trades"], "expectancy_price_r": stats["expectancy_price_r"],
                         "expectancy_all_in_r": stats["expectancy_all_in_r"], "profit_factor": stats["profit_factor"],
                         "net_pnl_btc": stats["net_pnl_btc"], "max_drawdown_btc_pct": stats["max_drawdown_btc_pct"]})
    return pd.DataFrame(rows)


def signal_consistency(frames: dict[str, pd.DataFrame]) -> dict:
    keys = {name: set(zip(frame.entry_time.astype(str), frame.direction, frame.exit_time.astype(str), frame.exit_reason)) for name, frame in frames.items()}
    reference = keys["A_0fee_0slip"]
    details = {}
    for name, values in keys.items():
        details[name] = {"same_entry_signals": len(reference & values), "different_entry_signals": len(reference ^ values), "exact_match": values == reference}
    return {"reference": "A_0fee_0slip", "signal_count": len(reference), "scenarios": details}


def cost_drag(summaries: dict[str, dict]) -> dict:
    a, b, c, d = (summaries[key] for key in SCENARIOS)
    gross_profit = a["gross_profit_btc"]
    trades = a["trades"]
    return {
        "primary_metric": "expectancy_all_in_r",
        "fee_drag_r_per_trade": b["expectancy_all_in_r"] - a["expectancy_all_in_r"],
        "slippage_drag_r_per_trade": c["expectancy_all_in_r"] - a["expectancy_all_in_r"],
        "total_cost_drag_r_per_trade": d["expectancy_all_in_r"] - a["expectancy_all_in_r"],
        "fee_drag_btc": b["net_pnl_btc"] - a["net_pnl_btc"],
        "slippage_drag_btc": c["net_pnl_btc"] - a["net_pnl_btc"],
        "total_cost_drag_btc": d["net_pnl_btc"] - a["net_pnl_btc"],
        "fee_drag_btc_per_trade": (b["net_pnl_btc"] - a["net_pnl_btc"]) / trades,
        "slippage_drag_btc_per_trade": (c["net_pnl_btc"] - a["net_pnl_btc"]) / trades,
        "total_cost_drag_btc_per_trade": (d["net_pnl_btc"] - a["net_pnl_btc"]) / trades,
        "fee_as_pct_of_A_gross_profit": b["fee_btc"] / gross_profit * 100 if gross_profit else None,
        "slippage_as_pct_of_A_gross_profit": c["slippage_cost_btc"] / gross_profit * 100 if gross_profit else None,
        "fee_as_pct_of_B_gross_profit": b["fee_btc"] / b["gross_profit_btc"] * 100 if b["gross_profit_btc"] else None,
        "fee_as_pct_of_D_gross_profit": d["fee_btc"] / d["gross_profit_btc"] * 100 if d["gross_profit_btc"] else None,
        "slippage_as_pct_of_C_gross_profit": c["slippage_cost_btc"] / c["gross_profit_btc"] * 100 if c["gross_profit_btc"] else None,
        "slippage_as_pct_of_D_gross_profit": d["slippage_cost_btc"] / d["gross_profit_btc"] * 100 if d["gross_profit_btc"] else None,
        "interaction_note": "Scenario equity paths and integer quantities differ, so fee and slippage drags are counterfactual differences and are not strictly additive.",
    }


def old_corrected(old_path: Path, corrected: dict) -> tuple[pd.DataFrame, dict]:
    old = json.loads(old_path.read_text(encoding="utf-8"))["cost_scenarios"]["D_004fee_2bp"]
    rows = [
        ("Trades", old["trades"], corrected["trades"], corrected["trades"] - old["trades"], "count"),
        ("Win Rate", old["win_rate"], corrected["win_rate_net"], corrected["win_rate_net"] - old["win_rate"], "ratio"),
        ("Expectancy", old["expectancy_r"], corrected["expectancy_all_in_r"], corrected["expectancy_all_in_r"] - old["expectancy_r"], "R/trade"),
        ("Profit Factor", old["profit_factor"], corrected["profit_factor"], corrected["profit_factor"] - old["profit_factor"], "ratio"),
        ("Net PnL", old["net_pnl"], corrected["net_pnl_btc"], "not_comparable", "OLD=USD; CORRECTED=BTC"),
        ("Max Drawdown", old["max_drawdown"], corrected["max_drawdown_btc_pct"] / 100, corrected["max_drawdown_btc_pct"] / 100 - old["max_drawdown"], "ratio"),
        ("Final Equity", old["final_equity"], corrected["final_equity_btc"], "not_comparable", "OLD=USD; CORRECTED=BTC"),
    ]
    table = pd.DataFrame(rows, columns=["metric", "old", "corrected", "difference", "unit"])
    attribution = {
        "COIN-M inverse PnL correction": "无法隔离：OLD未保留逐项反事实账本",
        "position sizing correction": "无法隔离：与BTC权益路径和整数数量联动",
        "quantity integerization": "无法隔离：OLD与CORRECTED交易集合及权益路径不同",
        "slippage direction correction": "无法隔离：OLD诊断与仓位、计价同时变化",
        "fee valuation correction": "无法隔离：OLD为USD线性扣费，CORRECTED为BTC结算",
        "BTC-base ledger correction": "无法隔离：这是计价体系变更，不是单一加法项",
    }
    return table, attribution


def conclusions(summaries: dict[str, dict], drag: dict) -> dict:
    a, d = summaries["A_0fee_0slip"], summaries["D_004fee_2bp"]
    a_positive = a["expectancy_price_r"] > 0 and a["profit_factor"] > 1 and a["gross_pnl_btc"] > 0
    return {
        "1_zero_cost_has_positive_edge": a_positive,
        "1_evidence": {"expectancy_price_r": a["expectancy_price_r"], "profit_factor": a["profit_factor"], "gross_pnl_btc": a["gross_pnl_btc"]},
        "2_fee_expectancy_drag_r": drag["fee_drag_r_per_trade"],
        "3_slippage_expectancy_drag_r": drag["slippage_drag_r_per_trade"],
        "4_total_cost_expectancy_drag_r": drag["total_cost_drag_r_per_trade"],
        "5_primary_cause": "原始信号仅有很薄的毛优势，手续费和滑点共同将其转为明显负期望" if a_positive and d["expectancy_all_in_r"] < 0 else "原始信号本身为负期望",
        "6_old_vs_corrected": "主要差异来自计价和执行引擎整体修正；现有OLD产物不足以独立定量归因各修正项",
        "7_enter_next_diagnostic_stage": bool(a_positive and d["expectancy_all_in_r"] < 0),
        "7_reason": "零成本毛优势过薄且无法覆盖交易摩擦；下一阶段应诊断信号质量，不应进行参数矩阵优化" if a_positive else "零成本信号不具备正期望",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", type=Path, default=Path("reports/crypto-backtest/BASELINE_trades.csv"))
    parser.add_argument("--old", type=Path, default=Path("reports/crypto-backtest/diagnostics/diagnostic_summary.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/crypto-backtest/baseline-audit-v2"))
    parser.add_argument("--initial-usd", type=float, default=1000.0)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    signals = pd.read_csv(args.trades, parse_dates=["entry_time", "exit_time"]).sort_values(["entry_time", "direction"]).reset_index(drop=True)
    initial_btc = float(signals.initial_equity_btc.iloc[0])
    frames: dict[str, pd.DataFrame] = {}; summaries: dict[str, dict] = {}
    for name, config in SCENARIOS.items():
        frame = replay_scenario(signals, config["fee_rate"], config["slippage_bp"], initial_btc)
        summary = summarize(frame, initial_btc, args.initial_usd); assert_accounting(frame, summary)
        frame.to_csv(args.output / f"{name}_trades.csv", index=False)
        frames[name] = frame; summaries[name] = summary
    directions = direction_summary(frames, initial_btc, args.initial_usd); directions.to_csv(args.output / "direction_summary.csv", index=False)
    consistency = signal_consistency(frames)
    pd.DataFrame([{"scenario": key, **value} for key, value in consistency["scenarios"].items()]).to_csv(args.output / "signal_consistency.csv", index=False)
    drag = cost_drag(summaries)
    diff_table, attribution = old_corrected(args.old, summaries["D_004fee_2bp"]); diff_table.to_csv(args.output / "OLD_vs_CORRECTED.csv", index=False)
    report = {"accounting": {"primary_unit": "BTC", "usd_mark_rule": "equity BTC multiplied by each trade exit fill; initial USD fixed at CLI input", "risk_pct": RISK_PCT, "contract_spec": {"contract_size_usd": 100, "min_qty": 1, "max_qty": 60000, "step_size": 1}}, "scenarios": summaries, "cost_drag": drag, "signal_consistency": consistency, "direction_summary": directions.to_dict(orient="records"), "old_vs_corrected": diff_table.to_dict(orient="records"), "attribution": attribution, "conclusions": conclusions(summaries, drag), "assertions": {"ledger_all_scenarios": "PASS", "signal_sets_exact_match": all(item["exact_match"] for item in consistency["scenarios"].values())}, "paused": ["parameter optimization", "288 parameter matrix", "MAE/MFE", "trend strength", "Walk Forward", "Monte Carlo", "200 to 1000", "fixed 0.02", "Funding analysis"]}
    (args.output / "audit_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "signals": len(signals), "scenario_A_expectancy": summaries["A_0fee_0slip"]["expectancy_price_r"], "scenario_D_expectancy_all_in": summaries["D_004fee_2bp"]["expectancy_all_in_r"], "signal_sets_exact_match": report["assertions"]["signal_sets_exact_match"], "ledger": "PASS"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
