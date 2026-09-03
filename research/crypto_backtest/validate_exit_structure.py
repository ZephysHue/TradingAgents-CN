"""Validate four preregistered exit models on the frozen 1858 BASELINE entries."""
from __future__ import annotations

import argparse
import json
import math
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from audit_corrected_baseline import quantity_for_risk
from backtest import prepare, read_data
from coin_m_engine import ContractSpec, fee_btc, fill_prices, pnl_btc


SPEC = ContractSpec()
INITIAL_BTC = 1000.0 / 11785.0
MODELS = {
    "E0": {"activation_r": None, "protected_r": None, "description": "Original SL=-1R, TP=+2R"},
    "E1": {"activation_r": 0.5, "protected_r": 0.0, "description": "Completed bar reaches +0.5R; next bar SL=BE"},
    "E2": {"activation_r": 1.0, "protected_r": 0.0, "description": "Completed bar reaches +1R; next bar SL=BE"},
    "E3": {"activation_r": 1.0, "protected_r": 0.5, "description": "Completed bar reaches +1R; next bar SL=+0.5R"},
}
SCENARIOS = {"A": {"fee_rate": 0.0, "slippage_bp": 0.0}, "D": {"fee_rate": 0.0004, "slippage_bp": 2.0}}


def simulate_exits(signals: pd.DataFrame, bars: pd.DataFrame, model: str) -> pd.DataFrame:
    config = MODELS[model]; output = []
    bar_index = bars.index; highs = bars.high.to_numpy(); lows = bars.low.to_numpy()
    for signal_id, row in signals.iterrows():
        entry = float(row.raw_entry); original_sl = float(row.sl); tp = float(row.tp); risk = abs(entry - original_sl)
        active_stop = original_sl; activated = False; activation_time = None
        position = bar_index.get_indexer([row.entry_time])[0]
        if position < 0: raise AssertionError(f"entry bar missing for signal {signal_id}")
        exit_time = raw_exit = reason = None
        for bar_pos in range(position, len(bar_index)):
            ts = bar_index[bar_pos]; high = float(highs[bar_pos]); low = float(lows[bar_pos])
            if row.direction == "long":
                stop_hit = low <= active_stop; tp_hit = high >= tp
            else:
                stop_hit = high >= active_stop; tp_hit = low <= tp
            if stop_hit:
                exit_time = ts; raw_exit = active_stop
                if active_stop == original_sl: reason = "Original SL"
                elif config["protected_r"] == 0: reason = "Break-even Stop"
                else: reason = "Protected Profit Stop"
                break
            if tp_hit:
                exit_time = ts; raw_exit = tp; reason = "TP 2R"; break
            if config["activation_r"] is not None and not activated:
                threshold = entry + config["activation_r"] * risk if row.direction == "long" else entry - config["activation_r"] * risk
                threshold_hit = high >= threshold if row.direction == "long" else low <= threshold
                if threshold_hit:
                    activated = True; activation_time = ts
                    active_stop = entry + config["protected_r"] * risk if row.direction == "long" else entry - config["protected_r"] * risk
        if exit_time is None:
            raise AssertionError(f"no exit for signal {signal_id} model {model}")
        output.append({"signal_id": signal_id, "model": model, "entry_time": row.entry_time, "exit_time": exit_time, "direction": row.direction, "raw_entry": entry, "initial_sl": original_sl, "tp": tp, "raw_exit": raw_exit, "exit_reason": reason, "protection_activated": activated, "activation_bar_time": activation_time})
    return pd.DataFrame(output)


def settle(exits: pd.DataFrame, scenario: str) -> pd.DataFrame:
    fee_rate = SCENARIOS[scenario]["fee_rate"]; slippage_bp = SCENARIOS[scenario]["slippage_bp"]; equity = INITIAL_BTC; rows = []
    for row in exits.itertuples(index=False):
        source = pd.Series({"direction": row.direction, "raw_entry": row.raw_entry, "sl": row.initial_sl})
        qty_raw, qty, risk_budget, entry_fill, initial_stop_fill = quantity_for_risk(equity, source, slippage_bp)
        _, exit_fill_d = fill_prices(row.direction, Decimal(str(row.raw_entry)), Decimal(str(row.raw_exit)), Decimal(str(slippage_bp / 10000))); exit_fill = float(exit_fill_d)
        if qty == 0:
            raw_pnl = filled_pnl = entry_fee = exit_fee = net = price_risk = all_in_risk = 0.0; price_r = all_in_r = np.nan
        else:
            raw_pnl = float(pnl_btc(row.direction, qty, Decimal(str(row.raw_entry)), Decimal(str(row.raw_exit)), SPEC))
            filled_pnl = float(pnl_btc(row.direction, qty, Decimal(str(entry_fill)), exit_fill_d, SPEC))
            entry_fee = float(fee_btc(qty, Decimal(str(entry_fill)), Decimal(str(fee_rate)), SPEC)); exit_fee = float(fee_btc(qty, exit_fill_d, Decimal(str(fee_rate)), SPEC))
            net = filled_pnl - entry_fee - exit_fee
            price_risk = abs(float(pnl_btc(row.direction, qty, Decimal(str(entry_fill)), Decimal(str(initial_stop_fill)), SPEC)))
            all_in_risk = price_risk + float(fee_btc(qty, Decimal(str(entry_fill)), Decimal(str(fee_rate)), SPEC)) + float(fee_btc(qty, Decimal(str(initial_stop_fill)), Decimal(str(fee_rate)), SPEC))
            price_r = net / price_risk; all_in_r = net / all_in_risk
        before = equity; equity += net
        rows.append({**row._asdict(), "scenario": scenario, "contracts_raw": qty_raw, "contracts": qty, "entry_fill": entry_fill, "exit_fill": exit_fill, "risk_budget_btc": risk_budget, "price_risk_btc": price_risk, "all_in_risk_btc": all_in_risk, "gross_raw_pnl_btc": raw_pnl, "filled_pnl_btc": filled_pnl, "fee_btc": entry_fee + exit_fee, "slippage_cost_btc": raw_pnl - filled_pnl, "net_pnl_btc": net, "price_r": price_r, "all_in_r": all_in_r, "equity_before_btc": before, "equity_after_btc": equity})
    return pd.DataFrame(rows)


def pf(values: pd.Series) -> float | None:
    gains = float(values[values > 0].sum()); losses = float(values[values <= 0].sum())
    return gains / abs(losses) if losses else None


def losing_streak(values: pd.Series) -> int:
    losses = values <= 0; groups = losses.ne(losses.shift()).cumsum(); lengths = values[losses].groupby(groups[losses]).size()
    return int(lengths.max()) if len(lengths) else 0


def underwater(frame: pd.DataFrame) -> tuple[float, dict]:
    equity = frame.equity_after_btc; dd = equity / equity.cummax() - 1; mask = dd < 0; groups = mask.ne(mask.shift()).cumsum(); periods = []
    for _, group in frame[mask].groupby(groups[mask]):
        periods.append({"trades": len(group), "days": float((group.exit_time.max() - group.entry_time.min()) / pd.Timedelta(days=1))})
    longest = max(periods, key=lambda item: item["days"]) if periods else {"trades": 0, "days": 0.0}
    return float(dd.min()), longest


def metrics(frame: pd.DataFrame) -> dict:
    r_col = "price_r" if frame.scenario.iloc[0] == "A" else "all_in_r"; values = frame[r_col].dropna(); max_dd, longest = underwater(frame)
    wins = values > 0
    return {"signals": len(frame), "trades": int((frame.contracts > 0).sum()), "skipped_min_qty": int((frame.contracts == 0).sum()), "win_rate": float(wins.mean()), "expectancy_price_r": float(frame.price_r.mean()), "expectancy_all_in_r": float(frame.all_in_r.mean()), "profit_factor": pf(frame.loc[frame.contracts > 0, "net_pnl_btc"]), "net_pnl_btc": float(frame.net_pnl_btc.sum()), "max_drawdown": max_dd, "avg_win_r": float(values[wins].mean()) if wins.any() else None, "avg_loss_r": float(abs(values[~wins].mean())) if (~wins).any() else None, "median_trade_r": float(values.median()), "max_consecutive_losses": losing_streak(values), "longest_underwater_days": longest["days"], "longest_underwater_trades": longest["trades"]}


def contributions(settled: dict[tuple[str, str], pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    e0a = settled[("E0", "A")].set_index("signal_id"); e0d = settled[("E0", "D")].set_index("signal_id"); rows = []; summary = {}
    for signal_id in e0a.index:
        row = {"signal_id": signal_id, "entry_time": e0a.at[signal_id, "entry_time"], "direction": e0a.at[signal_id, "direction"], "e0_exit_reason": e0a.at[signal_id, "exit_reason"], "e0_exit_time": e0a.at[signal_id, "exit_time"], "e0_a_r": e0a.at[signal_id, "price_r"], "e0_d_r": e0d.at[signal_id, "all_in_r"]}
        for model in ["E1", "E2", "E3"]:
            ma = settled[(model, "A")].set_index("signal_id"); md = settled[(model, "D")].set_index("signal_id")
            row.update({f"{model.lower()}_exit_reason": ma.at[signal_id, "exit_reason"], f"{model.lower()}_exit_time": ma.at[signal_id, "exit_time"], f"{model.lower()}_a_r": ma.at[signal_id, "price_r"], f"{model.lower()}_d_r": md.at[signal_id, "all_in_r"], f"{model.lower()}_delta_a_r": ma.at[signal_id, "price_r"] - e0a.at[signal_id, "price_r"], f"{model.lower()}_delta_d_r": md.at[signal_id, "all_in_r"] - e0d.at[signal_id, "all_in_r"]})
        rows.append(row)
    table = pd.DataFrame(rows)
    for model in ["E1", "E2", "E3"]:
        reason = table[f"{model.lower()}_exit_reason"]
        saved = table.e0_exit_reason.eq("Original SL") & reason.isin(["Break-even Stop", "Protected Profit Stop"])
        sacrificed = table.e0_exit_reason.eq("TP 2R") & ~reason.eq("TP 2R")
        saved_r = float(table.loc[saved, f"{model.lower()}_delta_a_r"].sum()); sacrificed_r = float((-table.loc[sacrificed, f"{model.lower()}_delta_a_r"]).sum())
        summary[model] = {"original_losses_changed_to_protected_exit": int(saved.sum()), "original_tp_winners_sacrificed": int(sacrificed.sum()), "saved_loss_r": saved_r, "sacrificed_winner_r": sacrificed_r, "net_protection_contribution_r": saved_r - sacrificed_r, "total_delta_a_r": float(table[f"{model.lower()}_delta_a_r"].sum()), "total_delta_d_r": float(table[f"{model.lower()}_delta_d_r"].sum())}
    return table, summary


def yearly(settled: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for model in MODELS:
        frame = settled[(model, "D")].copy(); frame["year"] = frame.entry_time.dt.year
        for year in range(2020, 2027):
            group = frame[frame.year == year]
            rows.append({"model": model, "year": year, "trades": len(group), "d_expectancy": float(group.all_in_r.mean()) if len(group) else None, "d_pf": pf(group.net_pnl_btc) if len(group) else None, "d_net_pnl_btc": float(group.net_pnl_btc.sum()), "d_max_drawdown": metrics(group)["max_drawdown"] if len(group) else 0.0})
    return pd.DataFrame(rows)


def half_stability(settled: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for model in MODELS:
        frame = settled[(model, "D")].sort_values("entry_time")
        for label, indexes in zip(["first_half", "second_half"], np.array_split(np.arange(len(frame)), 2)):
            group = frame.iloc[indexes]
            rows.append({"model": model, "half": label, "trades": len(group), "d_expectancy": float(group.all_in_r.mean()), "d_pf": pf(group.net_pnl_btc), "d_max_drawdown": metrics(group)["max_drawdown"]})
    return pd.DataFrame(rows)


def early_failure(signals: pd.DataFrame, bars: pd.DataFrame, e0: pd.DataFrame) -> pd.DataFrame:
    e0i = e0.set_index("signal_id"); rows = []
    for direction_scope in ["Combined", "Long", "Short"]:
        scoped = signals if direction_scope == "Combined" else signals[signals.direction == direction_scope.lower()]
        for horizon, offset in [(1, 0), (2, 1), (4, 3)]:
            values = []
            for signal_id, row in scoped.iterrows():
                ts = row.entry_time + pd.Timedelta(minutes=15 * offset)
                if ts >= e0i.at[signal_id, "exit_time"] or ts not in bars.index: continue
                close = float(bars.at[ts, "close"]); risk = abs(row.raw_entry - row.sl)
                unrealized = ((close - row.raw_entry) if row.direction == "long" else (row.raw_entry - close)) / risk
                values.append({"outcome": "Winner" if e0i.at[signal_id, "exit_reason"] == "TP 2R" else "Loser", "r": unrealized})
            data = pd.DataFrame(values)
            for outcome in ["All", "Winner", "Loser"]:
                sample = data.r if outcome == "All" else data.loc[data.outcome == outcome, "r"]
                rows.append({"direction": direction_scope, "horizon_bars": horizon, "future_outcome": outcome, "trades": len(sample), "mean_unrealized_r": float(sample.mean()), "p10": float(sample.quantile(.1)), "p25": float(sample.quantile(.25)), "median": float(sample.median()), "p75": float(sample.quantile(.75)), "p90": float(sample.quantile(.9))})
    return pd.DataFrame(rows)


def candidate_decisions(settled: dict[tuple[str, str], pd.DataFrame], yearly_table: pd.DataFrame, halves: pd.DataFrame, comparison: pd.DataFrame) -> dict:
    e0 = metrics(settled[("E0", "D")]); decisions = {}
    for model in ["E1", "E2", "E3"]:
        current = metrics(settled[(model, "D")]); improvement = current["expectancy_all_in_r"] - e0["expectancy_all_in_r"]
        half_model = halves[halves.model == model].set_index("half"); half_e0 = halves[halves.model == "E0"].set_index("half")
        halves_consistent = all(half_model.at[h, "d_expectancy"] > half_e0.at[h, "d_expectancy"] for h in ["first_half", "second_half"])
        ym = yearly_table[(yearly_table.model == model) & (yearly_table.trades >= 30)].set_index("year"); y0 = yearly_table[(yearly_table.model == "E0") & (yearly_table.trades >= 30)].set_index("year")
        common = ym.index.intersection(y0.index); years_not_worse = int((ym.loc[common, "d_expectancy"] >= y0.loc[common, "d_expectancy"] - .02).sum()); majority_years = years_not_worse >= math.ceil(len(common) / 2)
        delta_col = f"{model.lower()}_delta_d_r"; without_top10 = comparison.sort_values(delta_col, ascending=False).iloc[10:]
        tail_independent = float(without_top10[delta_col].mean()) > 0
        checks = {"d_expectancy_improvement_gt_002": improvement > .02, "d_pf_gt_1": current["profit_factor"] > 1, "max_dd_improved": current["max_drawdown"] > e0["max_drawdown"], "both_halves_improve_vs_e0": halves_consistent, "majority_years_not_worse_by_002": majority_years, "improvement_survives_removing_top10_contributors": tail_independent}
        decisions[model] = {"status": "Exit Hypothesis Candidate" if all(checks.values()) else "Reject", "checks": checks, "d_expectancy_delta": improvement, "years_evaluated": len(common), "years_not_worse": years_not_worse}
    return decisions


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--data-dir", type=Path, required=True); parser.add_argument("--trades", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    signals = pd.read_csv(args.trades, parse_dates=["entry_time", "exit_time"]).sort_values("entry_time").reset_index(drop=True)
    if len(signals) != 1858: raise AssertionError(f"expected 1858 frozen entries, got {len(signals)}")
    raw, _ = read_data(args.data_dir); bars = prepare(raw)["15m"]
    raw_exits = {model: simulate_exits(signals, bars, model) for model in MODELS}
    e0 = raw_exits["E0"]
    if not ((e0.exit_time.to_numpy() == signals.exit_time.to_numpy()).all() and (e0.exit_reason.replace({"Original SL": "SL", "TP 2R": "TP"}).to_numpy() == signals.exit_reason.to_numpy()).all()): raise AssertionError("E0 raw 15m replay does not match frozen BASELINE")
    settled = {(model, scenario): settle(raw_exits[model], scenario) for model in MODELS for scenario in SCENARIOS}
    model_rows = []
    for model in MODELS:
        for scenario in SCENARIOS:
            model_rows.append({"model": model, "scenario": scenario, "description": MODELS[model]["description"], **metrics(settled[(model, scenario)])})
    models = pd.DataFrame(model_rows); models.to_csv(args.output / "exit_models.csv", index=False)
    comparison, contribution_summary = contributions(settled); comparison.to_csv(args.output / "exit_trade_comparison.csv", index=False)
    yearly_table = yearly(settled); yearly_table.to_csv(args.output / "exit_yearly.csv", index=False)
    reason_rows = []
    for model in MODELS:
        counts = raw_exits[model].exit_reason.value_counts()
        for reason in ["Original SL", "Break-even Stop", "Protected Profit Stop", "TP 2R"]: reason_rows.append({"model": model, "exit_reason": reason, "trades": int(counts.get(reason, 0))})
    pd.DataFrame(reason_rows).to_csv(args.output / "exit_reason_summary.csv", index=False)
    early = early_failure(signals, bars, raw_exits["E0"]); early.to_csv(args.output / "early_failure_diagnostic.csv", index=False)
    halves = half_stability(settled)
    decisions = candidate_decisions(settled, yearly_table, halves, comparison)
    summary = {"frozen_entries": 1858, "simulation": {"timeframe": "15m", "protection_activation": "threshold touch confirmed at completed bar; new stop active from next bar", "same_bar_collision": "active stop first, then TP; newly triggered protection cannot act in the same bar", "e0_exact_match": True}, "models": {row["model"] + "_" + row["scenario"]: row for row in model_rows}, "exit_contributions": contribution_summary, "half_stability": halves.to_dict(orient="records"), "candidate_decisions": decisions, "early_failure_only_diagnostic": True, "entry_filters_added": False}
    (args.output / "exit_validation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "frozen_entries": len(signals), "e0_exact_match": True, "models": len(MODELS), "scenarios": len(SCENARIOS), "status": "PASS"}, ensure_ascii=False))
    return 0


if __name__ == "__main__": raise SystemExit(main())
