"""Exploratory signal-structure diagnostics for the fixed Corrected BASELINE."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import indicators, read_data, prepare


INITIAL_BTC = 1000.0 / 11785.0
MIN_SAMPLE_LABELS = [(30, "DISPLAY_ONLY"), (100, "LOW"), (300, "MEDIUM"), (math.inf, "RELATIVELY_HIGH")]


def confidence(n: int) -> str:
    for ceiling, label in MIN_SAMPLE_LABELS:
        if n < ceiling:
            return label
    return "RELATIVELY_HIGH"


def pf(values: pd.Series) -> float | None:
    gains = float(values[values > 0].sum()); losses = float(values[values <= 0].sum())
    return gains / abs(losses) if losses else None


def mdd(values: pd.Series) -> float:
    equity = INITIAL_BTC + values.cumsum()
    curve = pd.concat([pd.Series([INITIAL_BTC]), equity.reset_index(drop=True)], ignore_index=True)
    return float((curve / curve.cummax() - 1).min())


def stats(group: pd.DataFrame) -> dict:
    n = len(group)
    if not n:
        return {"trades": 0, "sample_confidence": confidence(0)}
    wins = group.a_r > 0; losses = ~wins
    return {
        "trades": n, "long": int((group.direction == "long").sum()), "short": int((group.direction == "short").sum()),
        "win_rate": float(wins.mean()), "avg_win_r": float(group.loc[wins, "a_r"].mean()) if wins.any() else None,
        "avg_loss_r": float(abs(group.loc[losses, "a_r"].mean())) if losses.any() else None,
        "expectancy_r": float(group.a_r.mean()), "profit_factor": pf(group.a_pnl_btc),
        "gross_pnl_btc": float(group.a_pnl_btc.sum()), "max_drawdown": mdd(group.a_pnl_btc),
        "scenario_d_all_in_expectancy": float(group.d_r.mean()), "scenario_d_profit_factor": pf(group.d_pnl_btc),
        "sample_confidence": confidence(n),
    }


def completed_align(frame: pd.DataFrame, index: pd.DatetimeIndex, delta: pd.Timedelta) -> pd.DataFrame:
    shifted = frame.copy(); shifted.index = shifted.index + delta
    return shifted.reindex(index, method="ffill")


def build_features(data_dir: Path, trades_path: Path, audit_dir: Path) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    raw, _ = read_data(data_dir); bars = prepare(raw)
    trades = pd.read_csv(trades_path, parse_dates=["entry_time", "exit_time"]).sort_values("entry_time").reset_index(drop=True)
    a = pd.read_csv(audit_dir / "A_0fee_0slip_trades.csv", parse_dates=["entry_time", "exit_time"])
    d = pd.read_csv(audit_dir / "D_004fee_2bp_trades.csv", parse_dates=["entry_time", "exit_time"])
    if len(trades) != len(a) or len(a) != len(d):
        raise AssertionError("baseline and scenario trade counts differ")
    features = trades[["entry_time", "exit_time", "direction", "raw_entry", "sl", "tp", "exit_reason"]].copy()
    features["signal_id"] = np.arange(len(features)); features["signal_time"] = features.entry_time - pd.Timedelta(minutes=15)
    features["a_r"] = a.price_r.to_numpy(); features["a_pnl_btc"] = a.net_pnl_btc.to_numpy()
    features["d_r"] = d.all_in_r.to_numpy(); features["d_pnl_btc"] = d.net_pnl_btc.to_numpy()

    signal_index = pd.DatetimeIndex(features.signal_time)
    m15 = bars["15m"].reindex(signal_index); h1 = completed_align(bars["1h"], signal_index, pd.Timedelta(hours=1)); h4 = completed_align(bars["4h"], signal_index, pd.Timedelta(hours=4))
    previous = bars["15m"].shift(1).reindex(signal_index)
    features["trend_strength_4h"] = (h4.ema20 - h4.ema50).abs().to_numpy() / h4.atr14.to_numpy()
    favorable_h1 = np.where(features.direction.eq("long"), h1.close.to_numpy() - h1.ema50.to_numpy(), h1.ema50.to_numpy() - h1.close.to_numpy())
    features["distance_1h"] = favorable_h1 / h1.atr14.to_numpy()
    features["trend_strength_15m"] = (m15.ema20 - m15.ema50).abs().to_numpy() / m15.atr14.to_numpy()
    features["pullback_depth"] = np.where(features.direction.eq("long"), m15.ema20.to_numpy() - m15.low.to_numpy(), m15.high.to_numpy() - m15.ema20.to_numpy()) / m15.atr14.to_numpy()
    features["confirmation_strength"] = np.where(features.direction.eq("long"), m15.close.to_numpy() - previous.high.to_numpy(), previous.low.to_numpy() - m15.close.to_numpy()) / m15.atr14.to_numpy()
    candle_range = m15.high.to_numpy() - m15.low.to_numpy()
    features["body_ratio"] = np.where(candle_range > 0, np.abs(m15.close.to_numpy() - m15.open.to_numpy()) / candle_range, np.nan)
    features["close_location"] = np.where(features.direction.eq("long"), m15.close.to_numpy() - m15.low.to_numpy(), m15.high.to_numpy() - m15.close.to_numpy()) / candle_range

    daily_raw = raw[["open", "high", "low", "close", "volume"]].resample("1D", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    daily = indicators(daily_raw); daily["ema200"] = daily.close.ewm(span=200, adjust=False, min_periods=200).mean(); daily["daily_vol"] = daily.atr14 / daily.close
    daily_aligned = completed_align(daily, signal_index, pd.Timedelta(days=1))
    q33, q66 = daily.daily_vol.quantile([1 / 3, 2 / 3]).tolist()
    trend = np.where(daily_aligned.close.to_numpy() > daily_aligned.ema200.to_numpy(), "Bull", "Bear").astype(object)
    trend[pd.isna(daily_aligned.ema200.to_numpy())] = "Unavailable"
    volatility = pd.cut(daily_aligned.daily_vol.to_numpy(), [-np.inf, q33, q66, np.inf], labels=["Low", "Mid", "High"], include_lowest=True).astype(object)
    volatility[pd.isna(daily_aligned.daily_vol.to_numpy())] = "Unavailable"
    features["daily_trend"] = trend
    features["daily_volatility"] = volatility.astype(str)
    features["market_state"] = features.daily_trend + "+" + features.daily_volatility
    features["year"] = features.entry_time.dt.year

    m15_index = bars["15m"].index
    path_rows = []
    for row in features.itertuples(index=False):
        risk = abs(row.raw_entry - row.sl)
        path = bars["15m"].loc[(m15_index >= row.entry_time) & (m15_index < row.exit_time)]
        if path.empty or risk <= 0:
            mfe = mae = np.nan
        elif row.direction == "long":
            mfe = (path.high.max() - row.raw_entry) / risk; mae = (row.raw_entry - path.low.min()) / risk
        else:
            mfe = (row.raw_entry - path.low.min()) / risk; mae = (path.high.max() - row.raw_entry) / risk
        reactions = {}
        for count, offset in [(1, 0), (2, 1), (4, 3)]:
            ts = row.entry_time + pd.Timedelta(minutes=15 * offset)
            if ts < row.exit_time and ts in m15_index:
                close = float(bars["15m"].at[ts, "close"])
                reactions[f"reaction_{count}bar_r"] = ((close - row.raw_entry) if row.direction == "long" else (row.raw_entry - close)) / risk
            else:
                reactions[f"reaction_{count}bar_r"] = np.nan
        bars_held = int((row.exit_time - row.entry_time) / pd.Timedelta(minutes=15)) + 1
        path_rows.append({"signal_id": row.signal_id, "mfe_r": mfe, "mae_r": mae, "holding_bars": bars_held, **reactions})
    features = features.merge(pd.DataFrame(path_rows), on="signal_id", how="left")
    metadata = {"daily_volatility_q33": q33, "daily_volatility_q66": q66, "bars": bars}
    return features, metadata


BUCKETS = {
    "trend_strength_4h": ([-np.inf, 0.25, 0.5, 0.75, 1, 1.5, 2, np.inf], ["0~0.25", "0.25~0.5", "0.5~0.75", "0.75~1", "1~1.5", "1.5~2", ">2"]),
    "distance_1h": ([-np.inf, 0, 0.25, 0.5, 1, 2, np.inf], ["<0", "0~0.25", "0.25~0.5", "0.5~1", "1~2", ">2"]),
    "trend_strength_15m": ([-np.inf, 0.1, 0.25, 0.5, 1, np.inf], ["0~0.1", "0.1~0.25", "0.25~0.5", "0.5~1", ">1"]),
    "pullback_depth": ([-np.inf, -0.2, 0, 0.1, 0.2, 0.3, 0.5, np.inf], ["<-0.2", "-0.2~0", "0~0.1", "0.1~0.2", "0.2~0.3", "0.3~0.5", ">0.5"]),
    "confirmation_strength": ([-np.inf, 0.05, 0.1, 0.2, 0.4, np.inf], ["0~0.05", "0.05~0.1", "0.1~0.2", "0.2~0.4", ">0.4"]),
    "body_ratio": ([-np.inf, 0.25, 0.5, 0.75, 1.0000001], ["0~0.25", "0.25~0.5", "0.5~0.75", "0.75~1"]),
    "close_location": ([-np.inf, 0.25, 0.5, 0.75, 1.0000001], ["0~0.25", "0.25~0.5", "0.5~0.75", "0.75~1"]),
    "holding_bars": ([0.5, 1.5, 2.5, 3.5, 4.5, 8.5, 16.5, 32.5, np.inf], ["1", "2", "3", "4", "5~8", "9~16", "17~32", ">32"]),
}


def bucket_table(features: pd.DataFrame) -> pd.DataFrame:
    output = []
    scopes = {"Combined": features, "Long": features[features.direction == "long"], "Short": features[features.direction == "short"]}
    for variable, (edges, labels) in BUCKETS.items():
        for scope, frame in scopes.items():
            assigned = pd.cut(frame[variable], edges, labels=labels, right=False, include_lowest=True)
            for label in labels:
                group = frame[assigned == label]
                output.append({"analysis": variable, "scope": scope, "bucket": label, **stats(group)})
    for scope, frame in scopes.items():
        for year, group in frame.groupby("year", dropna=False):
            output.append({"analysis": "year", "scope": scope, "bucket": str(year), **stats(group)})
        for state, group in frame.groupby("market_state", dropna=False):
            output.append({"analysis": "market_state", "scope": scope, "bucket": str(state), **stats(group)})
    for outcome, frame in [("All", features), ("Wins", features[features.a_r > 0]), ("Losses", features[features.a_r <= 0])]:
        edges, labels = BUCKETS["holding_bars"]
        assigned = pd.cut(frame.holding_bars, edges, labels=labels, right=False, include_lowest=True)
        for label in labels:
            output.append({"analysis": "holding_bars_by_outcome", "scope": outcome, "bucket": label, **stats(frame[assigned == label])})
    return pd.DataFrame(output)


def mae_mfe_summary(features: pd.DataFrame) -> dict:
    percentiles = [.05, .10, .25, .50, .75, .90, .95]
    result = {"distribution": {"mfe_r": features.mfe_r.quantile(percentiles).to_dict(), "mae_r": features.mae_r.quantile(percentiles).to_dict()}, "by_scope": {}}
    for scope, frame in [("Combined", features), ("Long", features[features.direction == "long"]), ("Short", features[features.direction == "short"]), ("Wins", features[features.a_r > 0]), ("Losses", features[features.a_r <= 0])]:
        result["by_scope"][scope] = {"trades": len(frame), "mfe_r": frame.mfe_r.quantile(percentiles).to_dict(), "mae_r": frame.mae_r.quantile(percentiles).to_dict()}
    sl = features[features.exit_reason == "SL"]; winners = features[features.exit_reason == "TP"]
    result["sl_trades_prior_favorable_excursion"] = {str(x): {"count": int((sl.mfe_r >= x).sum()), "ratio": float((sl.mfe_r >= x).mean())} for x in [.25, .5, .75, 1, 1.5]}
    result["tp_trades_prior_adverse_excursion"] = {str(x): {"count": int((winners.mae_r >= x).sum()), "ratio": float((winners.mae_r >= x).mean())} for x in [.25, .5, .75, 1]}
    result["p_tp_given_mfe"] = {}
    for x in [.25, .5, .75, 1, 1.25, 1.5]:
        eligible = features[features.mfe_r >= x]
        result["p_tp_given_mfe"][str(x)] = {"eligible": len(eligible), "tp_probability": float((eligible.exit_reason == "TP").mean()) if len(eligible) else None, "sample_confidence": confidence(len(eligible))}
    return result


def reaction_summary(features: pd.DataFrame) -> list[dict]:
    output = []
    for direction, scoped in [("Combined", features), ("Long", features[features.direction == "long"]), ("Short", features[features.direction == "short"])]:
        for column in ["reaction_1bar_r", "reaction_2bar_r", "reaction_4bar_r"]:
            for outcome, group in [("All", scoped), ("Wins", scoped[scoped.a_r > 0]), ("Losses", scoped[scoped.a_r <= 0])]:
                values = group[column].dropna()
                output.append({"direction": direction, "horizon": column, "outcome": outcome, "n": len(values), "mean": float(values.mean()), "p10": float(values.quantile(.1)), "p25": float(values.quantile(.25)), "median": float(values.median()), "p75": float(values.quantile(.75)), "p90": float(values.quantile(.9))})
    return output


def heatmaps(features: pd.DataFrame) -> pd.DataFrame:
    pairs = [("trend_strength_4h", "pullback_depth"), ("trend_strength_4h", "confirmation_strength"), ("trend_strength_15m", "pullback_depth")]
    output = []
    for x, y in pairs:
        xedges, xlabels = BUCKETS[x]; yedges, ylabels = BUCKETS[y]
        for scope, frame in [("Combined", features), ("Long", features[features.direction == "long"]), ("Short", features[features.direction == "short"])]:
            xb = pd.cut(frame[x], xedges, labels=xlabels, right=False, include_lowest=True); yb = pd.cut(frame[y], yedges, labels=ylabels, right=False, include_lowest=True)
            for xl in xlabels:
                for yl in ylabels:
                    group = frame[(xb == xl) & (yb == yl)]
                    s = stats(group)
                    output.append({"analysis": f"{x}_x_{y}", "scope": scope, "x_bucket": xl, "y_bucket": yl, "low_sample": len(group) < 30, **s})
    return pd.DataFrame(output)


def candidate_filters(features: pd.DataFrame) -> pd.DataFrame:
    definitions = [
        ("4H趋势强度>=0.5", features.trend_strength_4h >= .5, "预定义相邻中高趋势分桶的宽区间"),
        ("4H趋势强度>=0.75", features.trend_strength_4h >= .75, "排除最弱三个4H趋势分桶"),
        ("15m趋势强度>=0.25", features.trend_strength_15m >= .25, "排除EMA20/50接近的弱趋势区"),
        ("确认突破强度>=0.1ATR", features.confirmation_strength >= .1, "合并多个较强确认分桶"),
        ("确认实体比例>=0.5", features.body_ratio >= .5, "宽实体质量条件"),
        ("顺势收盘位置>=0.75", features.close_location >= .75, "确认K线靠近顺趋势端收盘"),
        ("回踩深度0~0.5ATR", features.pullback_depth.between(0, .5, inclusive="left"), "覆盖多个相邻中等回踩分桶"),
        ("回踩深度0.1~0.3ATR", features.pullback_depth.between(.1, .3, inclusive="left"), "两个相邻正期望回踩分桶，样本超过300"),
        ("Bear且Low/Mid波动", features.daily_trend.eq("Bear") & features.daily_volatility.isin(["Low", "Mid"]), "两个相邻波动状态均为正毛期望，合并样本超过300"),
    ]
    candidates = []
    overall = float(features.a_r.mean())
    for name, mask, rationale in definitions:
        kept, removed = features[mask.fillna(False)], features[~mask.fillna(False)]
        s = stats(kept); removed_s = stats(removed)
        candidates.append({"candidate": name, "trades_kept": len(kept), "share_pct": len(kept) / len(features) * 100, "trades_filtered": len(removed), "kept_expectancy_a": s.get("expectancy_r"), "filtered_expectancy_a": removed_s.get("expectancy_r"), "scenario_d_expectancy": s.get("scenario_d_all_in_expectancy"), "profit_factor_a": s.get("profit_factor"), "max_drawdown_a": s.get("max_drawdown"), "sample_confidence": confidence(len(kept)), "rationale": rationale, "improves_overall_a": s.get("expectancy_r", -np.inf) > overall})
    table = pd.DataFrame(candidates)
    eligible = table[(table.trades_kept >= 300) & table.improves_overall_a & (table.filtered_expectancy_a < overall) & ((table.kept_expectancy_a - table.filtered_expectancy_a) >= .03)].copy()
    eligible = eligible.sort_values(["scenario_d_expectancy", "trades_kept"], ascending=[False, False]).head(5)
    eligible["status"] = "Hypothesis Candidate"
    return eligible


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--data-dir", type=Path, required=True); parser.add_argument("--trades", type=Path, required=True); parser.add_argument("--audit-dir", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    features, metadata = build_features(args.data_dir, args.trades, args.audit_dir)
    required = ["trend_strength_4h", "distance_1h", "trend_strength_15m", "pullback_depth", "confirmation_strength", "body_ratio", "close_location", "mfe_r", "mae_r"]
    if features[required].isna().mean().max() > .15:
        raise AssertionError("feature missing rate exceeds 15%")
    buckets = bucket_table(features); heatmap = heatmaps(features); candidates = candidate_filters(features)
    features.to_csv(args.output / "mae_mfe.csv", index=False); buckets.to_csv(args.output / "diagnostic_buckets.csv", index=False); heatmap.to_csv(args.output / "heatmap_data.csv", index=False); candidates.to_csv(args.output / "candidate_filters.csv", index=False)
    summary = {"scope": {"scenario": "A primary, D supplemental", "trades": len(features), "multiple_testing": "Exploratory only; every candidate is Hypothesis Candidate and requires independent validation."}, "overall": stats(features), "mae_mfe": mae_mfe_summary(features), "initial_reaction": reaction_summary(features), "daily_volatility_thresholds": {"q33": metadata["daily_volatility_q33"], "q66": metadata["daily_volatility_q66"]}, "candidate_filters": candidates.to_dict(orient="records"), "files": ["diagnostic_buckets.csv", "mae_mfe.csv", "candidate_filters.csv", "heatmap_data.csv"]}
    (args.output / "diagnostic_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "trades": len(features), "buckets": len(buckets), "heatmap_cells": len(heatmap), "candidates": len(candidates), "status": "PASS"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
