"""Retrospective stability validation for the frozen Bear + Low/Mid candidate."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from audit_corrected_baseline import replay_scenario
from backtest import indicators, read_data


VOL_THRESHOLD = 0.04839
FEE_D = 0.0004
SLIPPAGE_D_BP = 2.0
INITIAL_USD = 1000.0
INITIAL_BTC = 1000.0 / 11785.0
VALIDATION_LABEL = "Retrospective Stability Validation / Pseudo-OOS Validation"


def completed_daily_features(data_dir: Path, signal_times: pd.DatetimeIndex) -> tuple[pd.DataFrame, dict]:
    raw, _ = read_data(data_dir)
    daily_raw = raw[["open", "high", "low", "close", "volume"]].resample("1D", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    daily = indicators(daily_raw)
    daily["ema200"] = daily.close.ewm(span=200, adjust=False, min_periods=200).mean()
    daily["daily_vol"] = daily.atr14 / daily.close
    shifted = daily.copy(); shifted.index = shifted.index + pd.Timedelta(days=1)
    aligned = shifted.reindex(signal_times, method="ffill")
    aligned.index = signal_times
    return aligned, {"daily_start": str(daily.index.min()), "daily_end": str(daily.index.max())}


def build_signal_table(data_dir: Path, trades_path: Path) -> pd.DataFrame:
    trades = pd.read_csv(trades_path, parse_dates=["entry_time", "exit_time"]).sort_values("entry_time").reset_index(drop=True)
    trades["original_signal_id"] = np.arange(len(trades)); trades["signal_time"] = trades.entry_time - pd.Timedelta(minutes=15)
    daily, _ = completed_daily_features(data_dir, pd.DatetimeIndex(trades.signal_time))
    trades["daily_close"] = daily.close.to_numpy(); trades["daily_ema200"] = daily.ema200.to_numpy(); trades["daily_atr14"] = daily.atr14.to_numpy(); trades["daily_volatility"] = daily.daily_vol.to_numpy()
    trades["bear_distance"] = (trades.daily_ema200 - trades.daily_close) / trades.daily_atr14
    trades["candidate"] = (trades.daily_close < trades.daily_ema200) & (trades.daily_volatility < VOL_THRESHOLD)
    return trades


def profit_factor(values: pd.Series) -> float | None:
    gains = float(values[values > 0].sum()); losses = float(values[values <= 0].sum())
    return gains / abs(losses) if losses else None


def max_drawdown_from_frame(frame: pd.DataFrame, initial_btc: float = INITIAL_BTC) -> float:
    if frame.empty: return 0.0
    equity = initial_btc + frame.net_pnl_btc.cumsum()
    curve = pd.concat([pd.Series([initial_btc]), equity.reset_index(drop=True)], ignore_index=True)
    return float((curve / curve.cummax() - 1).min())


def scenario_metrics(frame: pd.DataFrame, r_column: str) -> dict:
    if frame.empty:
        return {"trades": 0, "expectancy": None, "profit_factor": None, "net_pnl_btc": 0.0, "max_drawdown": 0.0, "win_rate": None, "avg_win_r": None, "avg_loss_r": None}
    values = frame[r_column]
    return {"trades": len(frame), "expectancy": float(values.mean()), "profit_factor": profit_factor(frame.net_pnl_btc), "net_pnl_btc": float(frame.net_pnl_btc.sum()), "max_drawdown": max_drawdown_from_frame(frame), "win_rate": float((values > 0).mean()), "avg_win_r": float(values[values > 0].mean()) if (values > 0).any() else None, "avg_loss_r": float(abs(values[values <= 0].mean())) if (values <= 0).any() else None}


def replay_pair(signals: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    source = signals.copy().set_index("original_signal_id", drop=False)
    a = replay_scenario(source, 0.0, 0.0, INITIAL_BTC); d = replay_scenario(source, FEE_D, SLIPPAGE_D_BP, INITIAL_BTC)
    a["original_signal_id"] = a.signal_id; d["original_signal_id"] = d.signal_id
    return a, d


def paired_metrics(signals: pd.DataFrame) -> dict:
    if signals.empty:
        return {"trades": 0, "long": 0, "short": 0, "win_rate": None, "scenario_a": scenario_metrics(pd.DataFrame(), "price_r"), "scenario_d": scenario_metrics(pd.DataFrame(), "all_in_r")}
    a, d = replay_pair(signals)
    return {"trades": len(signals), "long": int((signals.direction == "long").sum()), "short": int((signals.direction == "short").sum()), "win_rate": float((a.price_r > 0).mean()), "scenario_a": scenario_metrics(a, "price_r"), "scenario_d": scenario_metrics(d, "all_in_r")}


def annual_validation(candidate: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for year in range(2020, 2027):
        signals = candidate[candidate.entry_time.dt.year == year]
        result = paired_metrics(signals)
        rows.append({"year": year, "validation": VALIDATION_LABEL, "trades": result["trades"], "long": result["long"], "short": result["short"], "win_rate": result["win_rate"], "a_expectancy": result["scenario_a"]["expectancy"], "d_expectancy": result["scenario_d"]["expectancy"], "a_pf": result["scenario_a"]["profit_factor"], "d_pf": result["scenario_d"]["profit_factor"], "a_net_pnl_btc": result["scenario_a"]["net_pnl_btc"], "d_net_pnl_btc": result["scenario_d"]["net_pnl_btc"], "a_max_drawdown": result["scenario_a"]["max_drawdown"], "d_max_drawdown": result["scenario_d"]["max_drawdown"], "a_avg_win_r": result["scenario_a"]["avg_win_r"], "a_avg_loss_r": result["scenario_a"]["avg_loss_r"], "d_avg_win_r": result["scenario_d"]["avg_win_r"], "d_avg_loss_r": result["scenario_d"]["avg_loss_r"]})
    return pd.DataFrame(rows)


def rolling_windows(candidate: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rows = []; start = pd.Timestamp("2020-09-01", tz="UTC"); final_end = pd.Timestamp("2026-09-01", tz="UTC")
    while start + pd.DateOffset(months=12) <= final_end:
        end = start + pd.DateOffset(months=12); signals = candidate[(candidate.entry_time >= start) & (candidate.entry_time < end)]; result = paired_metrics(signals)
        rows.append({"start": start.isoformat(), "end_exclusive": end.isoformat(), "trades": result["trades"], "a_expectancy": result["scenario_a"]["expectancy"], "d_expectancy": result["scenario_d"]["expectancy"], "a_pf": result["scenario_a"]["profit_factor"], "d_pf": result["scenario_d"]["profit_factor"], "d_max_drawdown": result["scenario_d"]["max_drawdown"]})
        start += pd.DateOffset(months=6)
    frame = pd.DataFrame(rows); valid = frame[frame.trades > 0]
    summary = {"windows": len(frame), "windows_with_trades": len(valid), "positive_d_expectancy_windows": int((valid.d_expectancy > 0).sum()), "positive_d_expectancy_ratio": float((valid.d_expectancy > 0).mean()), "d_pf_above_1_windows": int((valid.d_pf > 1).sum()), "d_pf_above_1_ratio": float((valid.d_pf > 1).mean()), "a_and_d_positive_windows": int(((valid.a_expectancy > 0) & (valid.d_expectancy > 0)).sum()), "a_and_d_positive_ratio": float(((valid.a_expectancy > 0) & (valid.d_expectancy > 0)).mean())}
    return frame, summary


def walk_forward(candidate: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rows = []; train_start = pd.Timestamp("2020-09-01", tz="UTC"); final_end = pd.Timestamp("2026-09-01", tz="UTC"); fold = 1
    while train_start + pd.DateOffset(months=30) <= final_end:
        train_end = train_start + pd.DateOffset(months=24); test_end = train_end + pd.DateOffset(months=6)
        train = candidate[(candidate.entry_time >= train_start) & (candidate.entry_time < train_end)]; test = candidate[(candidate.entry_time >= train_end) & (candidate.entry_time < test_end)]; result = paired_metrics(test)
        rows.append({"fold": fold, "validation": "Pseudo-OOS Stability Check", "train_start": train_start.isoformat(), "train_end_exclusive": train_end.isoformat(), "train_trades_observed": len(train), "test_start": train_end.isoformat(), "test_end_exclusive": test_end.isoformat(), "trades": result["trades"], "a_expectancy": result["scenario_a"]["expectancy"], "d_expectancy": result["scenario_d"]["expectancy"], "a_pf": result["scenario_a"]["profit_factor"], "d_pf": result["scenario_d"]["profit_factor"], "a_max_drawdown": result["scenario_a"]["max_drawdown"], "d_max_drawdown": result["scenario_d"]["max_drawdown"], "a_net_pnl_btc": result["scenario_a"]["net_pnl_btc"], "d_net_pnl_btc": result["scenario_d"]["net_pnl_btc"]})
        fold += 1; train_start += pd.DateOffset(months=6)
    frame = pd.DataFrame(rows)
    combined_ids = []
    for row in rows:
        start = pd.Timestamp(row["test_start"]); end = pd.Timestamp(row["test_end_exclusive"])
        combined_ids.extend(candidate[(candidate.entry_time >= start) & (candidate.entry_time < end)].original_signal_id.tolist())
    combined = candidate[candidate.original_signal_id.isin(combined_ids)].sort_values("entry_time")
    return frame, paired_metrics(combined)


def threshold_sensitivity(all_signals: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for threshold in [0.040, 0.045, VOL_THRESHOLD, 0.050, 0.055]:
        signals = all_signals[(all_signals.daily_close < all_signals.daily_ema200) & (all_signals.daily_volatility < threshold)]
        result = paired_metrics(signals)
        rows.append({"threshold": threshold, "official_candidate": threshold == VOL_THRESHOLD, "trades": result["trades"], "a_expectancy": result["scenario_a"]["expectancy"], "d_expectancy": result["scenario_d"]["expectancy"], "a_pf": result["scenario_a"]["profit_factor"], "d_pf": result["scenario_d"]["profit_factor"], "d_max_drawdown": result["scenario_d"]["max_drawdown"]})
    return pd.DataFrame(rows)


def bear_distance(candidate: pd.DataFrame) -> pd.DataFrame:
    edges = [0, .25, .5, 1, 2, np.inf]; labels = ["0~0.25", "0.25~0.5", "0.5~1", "1~2", ">2"]
    assigned = pd.cut(candidate.bear_distance, edges, labels=labels, right=False, include_lowest=True); rows = []
    for label in labels:
        result = paired_metrics(candidate[assigned == label])
        rows.append({"bear_distance": label, "trades": result["trades"], "a_expectancy": result["scenario_a"]["expectancy"], "d_expectancy": result["scenario_d"]["expectancy"], "a_pf": result["scenario_a"]["profit_factor"], "d_pf": result["scenario_d"]["profit_factor"]})
    return pd.DataFrame(rows)


def direction_validation(candidate: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for direction in ["long", "short"]:
        result = paired_metrics(candidate[candidate.direction == direction])
        rows.append({"direction": direction, "trades": result["trades"], "a_expectancy": result["scenario_a"]["expectancy"], "d_expectancy": result["scenario_d"]["expectancy"], "a_pf": result["scenario_a"]["profit_factor"], "d_pf": result["scenario_d"]["profit_factor"], "a_max_drawdown": result["scenario_a"]["max_drawdown"], "d_max_drawdown": result["scenario_d"]["max_drawdown"]})
    return pd.DataFrame(rows)


def single_cost_metrics(candidate: pd.DataFrame, fee_rate: float, slippage_bp: float) -> dict:
    frame = replay_scenario(candidate.copy().set_index("original_signal_id", drop=False), fee_rate, slippage_bp, INITIAL_BTC)
    return {"expectancy_r": float(frame.all_in_r.mean()), "profit_factor": profit_factor(frame.net_pnl_btc), "net_pnl_btc": float(frame.net_pnl_btc.sum()), "max_drawdown": max_drawdown_from_frame(frame)}


def cost_matrix(candidate: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for fee in [.0002, .0004, .0005, .0006]:
        for slip in [0, 1, 2, 3, 5]:
            rows.append({"fee_rate": fee, "slippage_bp": slip, **single_cost_metrics(candidate, fee, slip)})
    return pd.DataFrame(rows)


def break_even(candidate: pd.DataFrame, mode: str) -> float:
    low, high = (0.0, .003) if mode == "fee" else (0.0, 30.0)
    for _ in range(40):
        mid = (low + high) / 2
        value = single_cost_metrics(candidate, mid, SLIPPAGE_D_BP)["expectancy_r"] if mode == "fee" else single_cost_metrics(candidate, FEE_D, mid)["expectancy_r"]
        if value > 0: low = mid
        else: high = mid
    return (low + high) / 2


def bootstrap(candidate: pd.DataFrame, runs: int = 10000) -> dict:
    _, d = replay_pair(candidate); returns = d.all_in_r.to_numpy(); rng = np.random.default_rng(20260830)
    samples = rng.choice(returns, size=(runs, len(returns)), replace=True); expectations = samples.mean(axis=1)
    gains = np.where(samples > 0, samples, 0).sum(axis=1); losses = np.abs(np.where(samples <= 0, samples, 0).sum(axis=1)); pfs = np.divide(gains, losses, out=np.full_like(gains, np.nan), where=losses > 0)
    equity = np.cumprod(1 + .01 * samples, axis=1); drawdowns = (equity / np.maximum.accumulate(equity, axis=1) - 1).min(axis=1)
    quantiles = [0.05, .25, .5, .75, .95]
    return {"runs": runs, "trades_per_run": len(returns), "mean_expectancy": float(expectations.mean()), "median_expectancy": float(np.median(expectations)), "expectancy_quantiles": {str(q): float(np.quantile(expectations, q)) for q in quantiles}, "p_expectancy_gt_0": float((expectations > 0).mean()), "p_pf_gt_1": float((pfs > 1).mean()), "max_drawdown_quantiles": {str(q): float(np.quantile(drawdowns, q)) for q in quantiles}, "note": "Bootstrap measures uncertainty in the observed sample; it is not future proof."}


def split_stability(candidate: pd.DataFrame) -> pd.DataFrame:
    ordered = candidate.sort_values("entry_time"); rows = []
    for parts, names in [(2, ["first_half", "second_half"]), (3, ["first_third", "middle_third", "last_third"])]:
        for name, indexes in zip(names, np.array_split(np.arange(len(ordered)), parts)):
            signals = ordered.iloc[indexes]; result = paired_metrics(signals)
            rows.append({"split_scheme": f"{parts}_parts", "segment": name, "start": signals.entry_time.min().isoformat(), "end": signals.entry_time.max().isoformat(), "trades": result["trades"], "a_expectancy": result["scenario_a"]["expectancy"], "d_expectancy": result["scenario_d"]["expectancy"], "a_pf": result["scenario_a"]["profit_factor"], "d_pf": result["scenario_d"]["profit_factor"], "d_max_drawdown": result["scenario_d"]["max_drawdown"]})
    return pd.DataFrame(rows)


def concentration(candidate: pd.DataFrame) -> pd.DataFrame:
    _, d = replay_pair(candidate); total = float(d.net_pnl_btc.sum()); rows = []
    winners = d[d.net_pnl_btc > 0].sort_values("net_pnl_btc", ascending=False)
    for top in [5, 10, 20]:
        removed_ids = set(winners.head(top).signal_id); remaining = d[~d.signal_id.isin(removed_ids)]
        ratio = float(winners.head(top).net_pnl_btc.sum() / total) if total else None
        rows.append({"top_winners": top, "top_profit_btc": float(winners.head(top).net_pnl_btc.sum()), "multiple_of_total_net_profit": ratio, "share_of_total_net_profit_pct": ratio * 100 if ratio is not None else None, "remaining_trades": len(remaining), "remaining_d_expectancy": float(remaining.all_in_r.mean()), "remaining_d_pf": profit_factor(remaining.net_pnl_btc)})
    return pd.DataFrame(rows)


def streaks_and_drawdown(candidate: pd.DataFrame, annual: pd.DataFrame) -> dict:
    _, d = replay_pair(candidate); losses = d.all_in_r <= 0; runs = losses.ne(losses.shift()).cumsum(); lengths = d[losses].groupby(runs[losses]).size()
    equity = INITIAL_BTC + d.net_pnl_btc.cumsum(); dd = equity / equity.cummax() - 1; underwater = dd < 0; groups = underwater.ne(underwater.shift()).cumsum(); periods = []
    for _, group in d[underwater].groupby(groups[underwater]):
        periods.append({"trades": len(group), "days": float((group.exit_time.max() - group.entry_time.min()) / pd.Timedelta(days=1))})
    longest = max(periods, key=lambda x: x["days"]) if periods else {"trades": 0, "days": 0.0}
    return {"max_consecutive_losses": int(lengths.max()), "p95_losing_streak_length": float(lengths.quantile(.95)), "max_drawdown": float(dd.min()), "average_underwater_drawdown": float(dd[dd < 0].mean()), "longest_underwater": longest, "annual_max_drawdown": {str(int(row.year)): row.d_max_drawdown for row in annual.itertuples()}}


def baseline_comparison(all_signals: pd.DataFrame, candidate: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, signals in [("BASELINE_ALL", all_signals), ("FROZEN_CANDIDATE", candidate)]:
        result = paired_metrics(signals)
        rows.append({"group": name, "trades": result["trades"], "a_expectancy": result["scenario_a"]["expectancy"], "d_expectancy": result["scenario_d"]["expectancy"], "a_pf": result["scenario_a"]["profit_factor"], "d_pf": result["scenario_d"]["profit_factor"], "d_net_pnl_btc": result["scenario_d"]["net_pnl_btc"], "d_max_drawdown": result["scenario_d"]["max_drawdown"]})
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--data-dir", type=Path, required=True); parser.add_argument("--trades", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    all_signals = build_signal_table(args.data_dir, args.trades); candidate = all_signals[all_signals.candidate].copy()
    if len(candidate) != 368: raise AssertionError(f"frozen candidate expected 368 trades, got {len(candidate)}")
    annual = annual_validation(candidate); rolling, rolling_summary = rolling_windows(candidate); wf, wf_combined = walk_forward(candidate); thresholds = threshold_sensitivity(all_signals); distance = bear_distance(candidate); directions = direction_validation(candidate); costs = cost_matrix(candidate); splits = split_stability(candidate); contribution = concentration(candidate); comparison = baseline_comparison(all_signals, candidate)
    for name, frame in [("annual_validation.csv", annual), ("rolling_12m.csv", rolling), ("walk_forward_folds.csv", wf), ("threshold_sensitivity.csv", thresholds), ("bear_distance.csv", distance), ("direction_validation.csv", directions), ("cost_matrix.csv", costs), ("split_stability.csv", splits), ("contribution_concentration.csv", contribution), ("baseline_comparison.csv", comparison)]: frame.to_csv(args.output / name, index=False)
    candidate.to_csv(args.output / "frozen_candidate_trades.csv", index=False)
    summary = {"validation_nature": VALIDATION_LABEL, "frozen_definition": {"bear": "completed Daily Close < completed Daily EMA200", "volatility": "completed Daily ATR14 / Daily Close < 0.04839", "threshold": VOL_THRESHOLD, "changed": False}, "candidate_trades": len(candidate), "full_history": paired_metrics(candidate), "annual": annual.to_dict(orient="records"), "rolling_summary": rolling_summary, "walk_forward_combined_tests": wf_combined, "threshold_sensitivity": thresholds.to_dict(orient="records"), "break_even": {"fee_rate_at_2bp": break_even(candidate, "fee"), "slippage_bp_at_004_fee": break_even(candidate, "slippage")}, "bootstrap": bootstrap(candidate), "streaks_and_drawdown": streaks_and_drawdown(candidate, annual), "contribution": contribution.to_dict(orient="records"), "multiple_testing": "Candidate was discovered on the full 2020-08 to 2026-08 history. These results are retrospective stability checks, not independent OOS proof."}
    (args.output / "candidate_validation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "validation": VALIDATION_LABEL, "candidate_trades": len(candidate), "rolling_windows": len(rolling), "walk_forward_folds": len(wf), "bootstrap_runs": 10000, "status": "PASS"}, ensure_ascii=False))
    return 0


if __name__ == "__main__": raise SystemExit(main())
