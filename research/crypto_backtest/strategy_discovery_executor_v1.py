"""Frozen, event-driven executor for the S1/S2/S3 discovery sprint.

All decisions are made on completed bars; execution is always on a later open.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

FEE, SLIP, INITIAL_EQUITY = 0.0004, 0.0002, 100000.0
NOTIONAL = INITIAL_EQUITY / 3 / 10
FLAGS = ["exploratory_survivorship_bias_present", "funding_not_modeled_not_deployable"]
STAGES = {
    "development": ("2026-06-01 00:00", "2026-06-30 23:45"),
    "validation": ("2026-07-01 00:00", "2026-07-31 23:45"),
    "holdout": ("2026-08-01 00:00", "2026-08-29 23:45"),
}
LEDGER_COLUMNS = ["trade_id", "strategy", "stage", "symbol", "tier", "side", "signal_timestamp_utc", "entry_timestamp_utc", "exit_timestamp_utc", "entry_raw_open", "entry_fill_price", "exit_raw_open", "exit_fill_price", "entry_fee", "exit_fee", "gross_pnl", "net_pnl", "gross_return_bps", "net_return_bps", "holding_bars", "exit_reason", "funding_unmodeled"]


def _ts(value):
    return pd.Timestamp(value, tz="UTC")


def _fill(raw, side, entry, slip=SLIP):
    return raw * (1 + slip if (side == "long") == entry else 1 - slip)


def _csv(rows, path, columns=None):
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def indicators(frame):
    f = frame.copy()
    f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True)
    f = f.sort_values("timestamp").reset_index(drop=True)
    close = f.close
    f["mid"] = close.rolling(20).mean()
    f["std"] = close.rolling(20).std(ddof=0)
    f["lo"], f["hi"] = f.mid - 2 * f["std"], f.mid + 2 * f["std"]
    delta = close.diff()
    for n in (7, 14):
        up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
        down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
        f[f"rsi{n}"] = 100 - 100 / (1 + up / down)
    tr = pd.concat([f.high - f.low, (f.high - close.shift()).abs(), (f.low - close.shift()).abs()], axis=1).max(axis=1)
    f["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    f["ema20"] = close.ewm(span=20, adjust=False).mean()
    hourly = f.set_index("timestamp").resample("1h", label="left", closed="left").agg(close=("close", "last")).dropna()
    hourly["e50"] = hourly.close.ewm(span=50, adjust=False).mean()
    hourly["e200"] = hourly.close.ewm(span=200, adjust=False).mean()
    # The bar labelled 10:00 closes at 11:00 and is unavailable before then.
    f["hour_key"] = f.timestamp.dt.floor("1h") - pd.Timedelta(hours=1)
    return f.merge(hourly[["e50", "e200"]], left_on="hour_key", right_index=True, how="left").drop(columns="hour_key")


def _complete(pos, raw, timestamp, reason, trade_id, fee=FEE, slip=SLIP):
    exit_fill = _fill(raw, pos["side"], False, slip)
    direction = 1 if pos["side"] == "long" else -1
    gross = NOTIONAL * direction * (exit_fill / pos["entry_fill_price"] - 1)
    exit_fee = NOTIONAL * fee
    net = gross - pos["entry_fee"] - exit_fee
    return {"trade_id": trade_id, "strategy": pos["strategy"], "stage": pos["stage"], "symbol": pos["symbol"], "tier": pos["tier"], "side": pos["side"], "signal_timestamp_utc": pos["signal_timestamp_utc"], "entry_timestamp_utc": pos["entry_timestamp_utc"], "exit_timestamp_utc": timestamp, "entry_raw_open": pos["entry_raw_open"], "entry_fill_price": pos["entry_fill_price"], "exit_raw_open": raw, "exit_fill_price": exit_fill, "entry_fee": pos["entry_fee"], "exit_fee": exit_fee, "gross_pnl": gross, "net_pnl": net, "gross_return_bps": gross / NOTIONAL * 1e4, "net_return_bps": net / NOTIONAL * 1e4, "holding_bars": pos["bars"], "exit_reason": reason, "funding_unmodeled": True}


def _life(event, timestamp, order):
    return {"event": event, "timestamp_utc": timestamp, **order}


def run_one(frame, strategy, stage, symbol, tier):
    """Execute one S1/S2 symbol; an exit fill blocks a same-point re-entry."""
    start, end = map(_ts, STAGES[stage])
    f = indicators(frame)
    f = f[f.timestamp <= end].reset_index(drop=True)
    trades, life, pos, pending, trade_id = [], [], None, None, 1
    for i in range(len(f) - 1):
        bar, nxt = f.iloc[i], f.iloc[i + 1]
        if bar.timestamp < start:
            continue
        exited_at_next_open = False
        if pending and pending["kind"] == "entry":
            if nxt.timestamp <= end:
                raw = float(nxt.open)
                pos = {**pending, "entry_timestamp_utc": nxt.timestamp, "entry_raw_open": raw, "entry_fill_price": _fill(raw, pending["side"], True), "entry_fee": NOTIONAL * FEE, "bars": 0}
                life.append(_life("filled_entry", nxt.timestamp, pending))
            else:
                life.append(_life("cancelled_missing_execution_bar", nxt.timestamp, pending))
            pending = None
        elif pending and pending["kind"] == "exit":
            if nxt.timestamp <= end:
                trades.append(_complete(pos, float(nxt.open), nxt.timestamp, pending["reason"], trade_id))
                trade_id += 1
                life.append(_life("filled_exit", nxt.timestamp, pending))
                pos = None
                exited_at_next_open = True
            pending = None
        if pos is not None:
            pos["bars"] += 1
            long = pos["side"] == "long"
            if strategy == "S1":
                hit = (bar.close >= bar.mid if long else bar.close <= bar.mid) or (bar.close <= pos["entry_fill_price"] - pos["atr"] if long else bar.close >= pos["entry_fill_price"] + pos["atr"]) or pos["bars"] >= 12
            else:
                hit = (bar.close <= pos["entry_fill_price"] - pos["atr"] if long else bar.close >= pos["entry_fill_price"] + pos["atr"]) or (bar.close >= pos["entry_fill_price"] + 1.5 * pos["atr"] if long else bar.close <= pos["entry_fill_price"] - 1.5 * pos["atr"]) or pos["bars"] >= 16
            if hit:
                pending = {"kind": "exit", "strategy": strategy, "stage": stage, "symbol": symbol, "tier": tier, "side": pos["side"], "reason": "rule_exit"}
                life.append(_life("pending_exit", bar.timestamp, pending))
            continue
        # bar t remained in the old position through the t+1 execution point.
        if exited_at_next_open or nxt.timestamp > end or pd.isna(bar.atr):
            continue
        if strategy == "S1":
            side = "long" if bar.close < bar.lo and bar.rsi7 < 20 else ("short" if bar.close > bar.hi and bar.rsi7 > 80 else None)
        else:
            side = "long" if bar.e50 > bar.e200 and bar.low <= bar.ema20 and bar.close > bar.ema20 and bar.rsi14 >= 50 else ("short" if bar.e50 < bar.e200 and bar.high >= bar.ema20 and bar.close < bar.ema20 and bar.rsi14 <= 50 else None)
        if side:
            pending = {"kind": "entry", "strategy": strategy, "stage": stage, "symbol": symbol, "tier": tier, "side": side, "signal_timestamp_utc": bar.timestamp, "atr": float(bar.atr)}
            life += [_life("created", bar.timestamp, pending), _life("pending_entry", bar.timestamp, pending)]
    if pos is not None:
        post = indicators(frame)
        post = post[post.timestamp > end]
        if len(post):
            terminal = post.iloc[0]
            trades.append(_complete(pos, float(terminal.open), terminal.timestamp, "terminal_forced_exit_next_open", trade_id))
            life.append(_life("filled_exit", terminal.timestamp, {"kind": "exit", "strategy": strategy, "stage": stage, "symbol": symbol, "tier": tier, "side": pos["side"], "reason": "terminal_forced_exit_next_open"}))
        else:
            life.append(_life("cancelled_missing_execution_bar", end, {"kind": "exit", "strategy": strategy, "stage": stage, "symbol": symbol, "tier": tier, "side": pos["side"], "reason": "terminal_forced_exit_next_open"}))
    return trades, life


def run_s3(frames, members, stage):
    start, end = map(_ts, STAGES[stage])
    hourly = {}
    for symbol, frame in frames.items():
        x = frame.copy(); x["timestamp"] = pd.to_datetime(x.timestamp, utc=True)
        hourly[symbol] = x.set_index("timestamp").resample("1h", label="left", closed="left").agg(open=("open", "first"), close=("close", "last")).dropna()
    by_tier = {tier: sorted((m for m in members if m["tier"] == tier), key=lambda m: m["symbol"]) for tier in ("hot", "mid", "low")}
    positions, trades, life, trade_id = {}, [], [], 1
    # signal_end is the completed hour end/open timestamp; return uses hour ending there.
    for execution in pd.date_range(start, end, freq="1h", tz="UTC"):
        for symbol, pos in list(positions.items()):
            if pos["exit_at"] == execution:
                if execution in hourly[symbol].index:
                    trades.append(_complete(pos, float(hourly[symbol].loc[execution, "open"]), execution, "four_complete_1h_bars", trade_id))
                    trade_id += 1; life.append(_life("filled_exit", execution, pos)); del positions[symbol]
                else:
                    life.append(_life("cancelled_missing_execution_bar", execution, pos)); del positions[symbol]
        signal_hour = execution - pd.Timedelta(hours=1)
        for tier, tier_members in by_tier.items():
            ranked = []
            for member in tier_members:
                symbol = member["symbol"]
                h = hourly[symbol]
                if signal_hour in h.index and signal_hour - pd.Timedelta(hours=24) in h.index:
                    ranked.append((float(h.loc[signal_hour, "close"] / h.loc[signal_hour - pd.Timedelta(hours=24), "close"] - 1), symbol))
            n = len(ranked) // 5
            if not n:
                continue
            ranked.sort(key=lambda item: (-item[0], item[1]))
            selected = [(symbol, "long") for _, symbol in ranked[:n]] + [(symbol, "short") for _, symbol in ranked[-n:]]
            for symbol, side in selected:
                exit_at = execution + pd.Timedelta(hours=4)
                if symbol in positions or execution > end or exit_at > end or execution not in hourly[symbol].index or exit_at not in hourly[symbol].index:
                    continue
                raw = float(hourly[symbol].loc[execution, "open"])
                pos = {"kind": "entry", "strategy": "S3", "stage": stage, "symbol": symbol, "tier": tier, "side": side, "signal_timestamp_utc": execution, "entry_timestamp_utc": execution, "entry_raw_open": raw, "entry_fill_price": _fill(raw, side, True), "entry_fee": NOTIONAL * FEE, "bars": 4, "exit_at": exit_at}
                positions[symbol] = pos
                life += [_life("created", execution, pos), _life("pending_entry", execution, pos), _life("filled_entry", execution, pos)]
    return trades, life


def metrics(trades, tier=None):
    x = pd.DataFrame(trades)
    if tier is not None and not x.empty:
        x = x[x.tier == tier]
    if x.empty:
        return {"trade_count": 0, "win_rate": np.nan, "net_expectancy_bps": np.nan, "profit_factor": np.nan, "net_pnl": 0.0, "avg_holding_bars": np.nan, "max_holding_bars": np.nan, "exposure": 0.0}
    pnl = x.net_pnl
    return {"trade_count": len(x), "win_rate": float((pnl > 0).mean() * 100), "net_expectancy_bps": float(x.net_return_bps.mean()), "profit_factor": float(pnl[pnl > 0].sum() / abs(pnl[pnl < 0].sum())) if (pnl < 0).any() else np.inf, "net_pnl": float(pnl.sum()), "avg_holding_bars": float(x.holding_bars.mean()), "max_holding_bars": int(x.holding_bars.max()), "exposure": float(len(x) * NOTIONAL)}


def passes(m, stage):
    return m["trade_count"] >= (150 if stage == "development" else 100) and m["win_rate"] > 50 and m["net_expectancy_bps"] > 0 and m["profit_factor"] > 1.05


def classify(m):
    if m["trade_count"] < 300: return "insufficient_holdout_sample"
    if m["win_rate"] > 50 and m["net_expectancy_bps"] <= 0: return "high_win_rate_negative_expectancy"
    return "exploratory_research_candidate" if m["win_rate"] > 50 and m["net_expectancy_bps"] > 0 and m["profit_factor"] > 1.10 and m.get("max_drawdown_pct", -100) > -25 else "rejected_holdout"


def equity(trades, strategy, stage, frames):
    start, end = map(_ts, STAGES[stage]); ledger = pd.DataFrame(trades); rows = []
    for timestamp in pd.date_range(start, end, freq="15min", tz="UTC"):
        done = ledger[ledger.exit_timestamp_utc <= timestamp] if not ledger.empty else ledger
        active = ledger[(ledger.entry_timestamp_utc <= timestamp) & (ledger.exit_timestamp_utc > timestamp)] if not ledger.empty else ledger
        realized = float(done.net_pnl.sum()) if len(done) else 0.0; unreal = 0.0
        for _, trade in active.iterrows():
            q = frames[trade.symbol]; close = q.loc[q.timestamp == timestamp, "close"]
            if len(close): unreal += NOTIONAL * (1 if trade.side == "long" else -1) * (float(close.iloc[0]) / trade.entry_fill_price - 1) - trade.entry_fee
        rows.append({"strategy": strategy, "stage": stage, "timestamp_utc": timestamp, "equity": INITIAL_EQUITY + realized + unreal, "realized_pnl": realized, "unrealized_pnl": unreal, "gross_exposure": len(active) * NOTIONAL, "active_positions": len(active)})
    return pd.DataFrame(rows)


def drawdown(curve):
    eq = curve.equity; peak = eq.cummax(); dd = eq / peak - 1; trough = dd.idxmin(); start = eq.iloc[:trough + 1].idxmax(); recovery = next((i for i in range(trough + 1, len(eq)) if eq.iloc[i] >= eq.iloc[start]), None); final = len(eq) - 1 if recovery is None else recovery
    return {"max_drawdown_pct": float(dd.iloc[trough] * 100), "drawdown_start_utc": curve.iloc[start].timestamp_utc, "drawdown_trough_utc": curve.iloc[trough].timestamp_utc, "recovery_utc": None if recovery is None else curve.iloc[recovery].timestamp_utc, "duration_15m_bars": final - start, "duration_natural": str(curve.iloc[final].timestamp_utc - curve.iloc[start].timestamp_utc)}


def _stress(ledger):
    if ledger.empty: return {"status": "not_run_due_to_previous_gate"}
    y = ledger.copy(); fee, slip = FEE * 1.5, SLIP * 1.5
    y.entry_fill_price = y.entry_raw_open * np.where(y.side == "long", 1 + slip, 1 - slip)
    y.exit_fill_price = y.exit_raw_open * np.where(y.side == "long", 1 - slip, 1 + slip)
    y.gross_pnl = NOTIONAL * np.where(y.side == "long", 1, -1) * (y.exit_fill_price / y.entry_fill_price - 1)
    y.net_pnl = y.gross_pnl - NOTIONAL * fee * 2; y.net_return_bps = y.net_pnl / NOTIONAL * 1e4
    result = metrics(y.to_dict("records")); result["status"] = "passed_cost_stress" if result["net_expectancy_bps"] > 0 and result["profit_factor"] > 1 else "rejected_cost_stress"; return result


def execute(manifest, universe, output):
    out = Path(output); out.mkdir(parents=True, exist_ok=True)
    data, uni = json.loads(Path(manifest).read_text(encoding="utf-8")), json.loads(Path(universe).read_text(encoding="utf-8"))
    root = Path.cwd()
    frames = {}
    for item in data["files"]:
        path = root / item["path"].replace("\\", "/")
        if path.exists():
            frame = pd.read_csv(path, compression="gzip", parse_dates=["timestamp"]); frame["timestamp"] = pd.to_datetime(frame.timestamp, utc=True); frames[item["symbol"]] = frame
    rows, curves, dds, registry, ledgers = [], [], [], [], {s: [] for s in ("S1", "S2", "S3")}; gates = {s: True for s in ledgers}
    for stage in STAGES:
        members = [m for m in uni["selected"] if m["stage"] == stage and m["symbol"] in frames]
        for strategy in ledgers:
            status, trades, life = "not_run_due_to_previous_gate", [], []
            if gates[strategy]:
                if strategy == "S3": trades, life = run_s3(frames, members, stage)
                else:
                    values = [run_one(frames[m["symbol"]], strategy, stage, m["symbol"], m["tier"]) for m in members]
                    trades = [t for pair in values for t in pair[0]]; life = [event for pair in values for event in pair[1]]
                status = "ran"; ledgers[strategy].extend(trades)
                curve = equity(trades, strategy, stage, frames); curves.append(curve); dd = drawdown(curve); dds.append({"strategy": strategy, "stage": stage, **dd})
                total = metrics(trades); total.update(dd)
                if stage != "holdout": gates[strategy] = passes(total, stage); status = "ran" if gates[strategy] else ("rejected_in_development" if stage == "development" else "rejected_in_validation")
                else: status = classify(total)
            _csv(life, out / f"_life_{strategy}_{stage}.csv")
            for tier in ("total", "hot", "mid", "low"):
                stat = metrics(trades, None if tier == "total" else tier)
                if tier == "total" and gates.get(strategy, False) and curves: stat.update(dd if 'dd' in locals() else {})
                rows.append({"strategy": strategy, "stage": stage, "tier": tier, "status": status, "warning_flags": ";".join(FLAGS), **stat})
            if stage == "holdout": registry.append({"strategy": strategy, "classification": status, "warning_flags": FLAGS})
    for strategy, ledger in ledgers.items(): _csv(ledger, out / f"trade_ledger_{strategy.lower()}.csv", LEDGER_COLUMNS)
    for strategy in ledgers:
        events = []
        for path in sorted(out.glob(f"_life_{strategy}_*.csv")):
            if path.stat().st_size: events.extend(pd.read_csv(path).to_dict("records"))
            path.unlink()
        _csv(events, out / f"order_lifecycle_{strategy.lower()}.csv")
    results = pd.DataFrame(rows)
    for stage in STAGES: results[results.stage == stage].to_csv(out / f"{stage}_results.csv", index=False)
    pd.concat(curves, ignore_index=True).to_csv(out / "equity_curves.csv", index=False) if curves else _csv([], out / "equity_curves.csv")
    _csv(dds, out / "drawdown_summary.csv")
    stress = []
    for strategy, ledger in ledgers.items():
        x = pd.DataFrame(ledger); x = x[x.stage == "holdout"] if not x.empty and "stage" in x else x
        stress.append({"strategy": strategy, "warning_flags": ";".join(FLAGS), **_stress(x)})
    _csv(stress, out / "cost_stress_results.csv")
    (out / "rejection_registry.json").write_text(json.dumps({"warning_flags": FLAGS, "results": registry}, indent=2) + "\n", encoding="utf-8")
    (out / "research_candidate_registry.json").write_text(json.dumps([x for x in registry if x["classification"] == "exploratory_research_candidate"], indent=2) + "\n", encoding="utf-8")
    return rows
