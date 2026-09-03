"""Run the frozen V1 signal rules on Bybit USDT linear perpetuals.

This is a separate linear-settlement study.  It does not alter the audited
Binance COIN-M engine or its frozen V1/V2/V3 research artifacts.
"""
from __future__ import annotations

import argparse, hashlib, json, math, time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from backtest import prepare

API = "https://api.bybit.com/v5/market/kline"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT")

@dataclass(frozen=True)
class Cost:
    fee_rate: float
    slippage_bp: float

def fetch_symbol(symbol: str, start: pd.Timestamp, end: pd.Timestamp, root: Path) -> tuple[pd.DataFrame, dict]:
    target = root / f"{symbol}-15m.csv"
    rows, cursor = [], int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    session = requests.Session()
    while cursor < end_ms:
        params = {"category": "linear", "symbol": symbol, "interval": "15", "start": cursor, "end": min(cursor + 1000 * 900_000 - 1, end_ms - 1), "limit": 1000}
        response = session.get(API, params=params, timeout=30); response.raise_for_status()
        body = response.json()
        if body.get("retCode") != 0: raise RuntimeError(f"{symbol}: {body.get('retMsg')}")
        page = body.get("result", {}).get("list", [])
        if not page:
            cursor += 1000 * 900_000; continue
        rows.extend(page); cursor = max(int(row[0]) for row in page) + 900_000
        time.sleep(0.06)
    frame = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "turnover"])
    if frame.empty: raise RuntimeError(f"{symbol}: no data")
    frame["open_time"] = pd.to_numeric(frame["open_time"]); frame = frame.drop_duplicates("open_time").sort_values("open_time")
    for column in ("open", "high", "low", "close", "volume", "turnover"): frame[column] = pd.to_numeric(frame[column])
    frame["timestamp"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    frame = frame[(frame.timestamp >= start) & (frame.timestamp < end)].copy()
    frame.to_csv(target, index=False)
    payload = target.read_bytes()
    return frame.set_index("timestamp"), {"symbol": symbol, "path": str(target), "sha256": hashlib.sha256(payload).hexdigest(), "bars": len(frame), "start": frame.timestamp.min().isoformat(), "end": frame.timestamp.max().isoformat()}

def fills(side: str, entry: float, exit: float, bp: float) -> tuple[float, float]:
    slip = bp / 10000
    return (entry * (1 + slip), exit * (1 - slip)) if side == "long" else (entry * (1 - slip), exit * (1 + slip))

def run_one(frame: pd.DataFrame, cost: Cost) -> pd.DataFrame:
    bars = prepare(frame[["open", "high", "low", "close", "volume"]])
    m15, h1, h4 = bars["15m"], bars["1h"], bars["4h"]
    h1c, h4c = h1.copy(), h4.copy(); h1c.index += pd.Timedelta(hours=1); h4c.index += pd.Timedelta(hours=4)
    a1, a4 = h1c.reindex(m15.index, method="ffill"), h4c.reindex(m15.index, method="ffill")
    work = m15.copy(); work["h1e50"], work["h4e20"], work["h4e50"] = a1.ema50, a4.ema20, a4.ema50
    long_trend = (work.h4e20 > work.h4e50) & (a1.close > work.h1e50) & (work.ema20 > work.ema50)
    short_trend = (work.h4e20 < work.h4e50) & (a1.close < work.h1e50) & (work.ema20 < work.ema50)
    lo, hi = work.ema20 - .2 * work.atr14, work.ema20 + .2 * work.atr14
    touch = (work.low <= hi) & (work.high >= lo)
    long_signal = long_trend & touch & (work.close.shift() > work.ema20.shift()) & (work.close > work.high.shift()) & (work.close > work.ema20)
    short_signal = short_trend & touch & (work.close.shift() < work.ema20.shift()) & (work.close < work.low.shift()) & (work.close < work.ema20)
    equity, position, trades, day_r, loss_streak, current_day = 1000.0, None, [], 0.0, 0, None
    rows = list(work.itertuples())
    for i in range(2, len(work) - 1):
        ts, row = work.index[i], rows[i]
        if current_day != ts.date(): current_day, day_r, loss_streak = ts.date(), 0.0, 0
        if position:
            raw_exit = reason = None
            if position["side"] == "long":
                if row.low <= position["sl"]: raw_exit, reason = position["sl"], "SL"
                elif row.high >= position["tp"]: raw_exit, reason = position["tp"], "TP"
            else:
                if row.high >= position["sl"]: raw_exit, reason = position["sl"], "SL"
                elif row.low <= position["tp"]: raw_exit, reason = position["tp"], "TP"
            if raw_exit is not None:
                entry_fill, exit_fill = fills(position["side"], position["entry_raw"], raw_exit, cost.slippage_bp)
                gross = position["sign"] * position["qty"] * (exit_fill - entry_fill)
                fees = position["qty"] * (entry_fill + exit_fill) * cost.fee_rate
                net = gross - fees; pnl_r = net / position["risk_usdt"]
                before = equity; equity += net; day_r += pnl_r; loss_streak = loss_streak + 1 if pnl_r < 0 else 0
                trades.append({"entry_time": position["entry_time"], "exit_time": ts.isoformat(), "direction": position["side"], "entry_raw": position["entry_raw"], "entry_fill": entry_fill, "stop_raw": position["sl"], "tp_raw": position["tp"], "exit_raw": raw_exit, "exit_fill": exit_fill, "exit_reason": reason, "quantity_base": position["qty"], "risk_usdt": position["risk_usdt"], "gross_pnl_usdt": gross, "fee_usdt": fees, "net_pnl_usdt": net, "pnl_r": pnl_r, "equity_before": before, "equity_after": equity})
                position = None; continue
        if position is None and day_r > -3 and loss_streak < 3:
            side = "long" if long_signal.iloc[i] else "short" if short_signal.iloc[i] else None
            if not side or not np.isfinite(row.atr14): continue
            entry = float(work.open.iloc[i + 1]); swing = row.confirmed_swing_low if side == "long" else row.confirmed_swing_high
            if not np.isfinite(swing): continue
            sl = swing - .3 * row.atr14 if side == "long" else swing + .3 * row.atr14; distance = entry - sl if side == "long" else sl - entry
            if distance <= 0: continue
            entry_fill, stop_fill = fills(side, entry, sl, cost.slippage_bp); risk_per_unit = abs(entry_fill - stop_fill) + cost.fee_rate * (entry_fill + stop_fill)
            risk = equity * .01; qty = risk / risk_per_unit
            if qty <= 0: continue
            position = {"side": side, "sign": 1 if side == "long" else -1, "entry_time": work.index[i + 1].isoformat(), "entry_raw": entry, "sl": float(sl), "tp": entry + 2 * distance if side == "long" else entry - 2 * distance, "qty": qty, "risk_usdt": risk}
    return pd.DataFrame(trades)

def summary(trades: pd.DataFrame) -> dict:
    if trades.empty: return {"trades": 0}
    wins, losses = trades[trades.pnl_r > 0], trades[trades.pnl_r <= 0]
    gp, gl = wins.net_pnl_usdt.sum(), losses.net_pnl_usdt.sum()
    curve = trades.equity_after; dd = (curve / curve.cummax() - 1).min()
    return {"trades": len(trades), "long_trades": int((trades.direction == "long").sum()), "short_trades": int((trades.direction == "short").sum()), "win_rate": float((trades.pnl_r > 0).mean()), "expectancy_r": float(trades.pnl_r.mean()), "profit_factor": float(gp / abs(gl)) if gl else math.inf, "net_pnl_usdt": float(trades.net_pnl_usdt.sum()), "fees_usdt": float(trades.fee_usdt.sum()), "max_drawdown_pct": float(dd), "final_equity_usdt": float(curve.iloc[-1]), "max_loss_streak": int((trades.pnl_r.le(0).astype(int).groupby(trades.pnl_r.gt(0).cumsum()).sum()).max())}

def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("--start", default="2023-01-01"); p.add_argument("--end", default="2026-08-29"); p.add_argument("--symbols", nargs="+", default=list(SYMBOLS)); p.add_argument("--data-dir", type=Path, default=Path("research/crypto_backtest/data/bybit-linear-v1")); p.add_argument("--output", type=Path, default=Path("reports/crypto-backtest/bybit-linear-v1-frozen-signal")); p.add_argument("--refresh", action="store_true"); a=p.parse_args(); a.data_dir.mkdir(parents=True, exist_ok=True); a.output.mkdir(parents=True, exist_ok=True)
    start, end = pd.Timestamp(a.start, tz="UTC"), pd.Timestamp(a.end, tz="UTC") + pd.Timedelta(days=1)
    records, all_summaries = [], []
    for symbol in a.symbols:
        path = a.data_dir / f"{symbol}-15m.csv"
        if path.exists() and not a.refresh:
            raw = pd.read_csv(path); raw["timestamp"] = pd.to_datetime(raw.timestamp, utc=True); frame = raw.set_index("timestamp"); payload = path.read_bytes(); rec = {"symbol": symbol, "path": str(path), "sha256": hashlib.sha256(payload).hexdigest(), "bars": len(frame), "start": frame.index.min().isoformat(), "end": frame.index.max().isoformat()}
        else: frame, rec = fetch_symbol(symbol, start, end, a.data_dir)
        records.append(rec)
        for scenario, cost in (("A", Cost(0.0, 0.0)), ("D", Cost(.0004, 2.0))):
            trades = run_one(frame, cost); trades.insert(0, "symbol", symbol); trades.to_csv(a.output / f"{symbol}_{scenario}_trades.csv", index=False)
            all_summaries.append({"symbol": symbol, "scenario": scenario, **summary(trades)})
    pd.DataFrame(all_summaries).to_csv(a.output / "bybit_linear_v1_summary.csv", index=False)
    manifest = {"source": "Bybit V5 public linear perpetual kline API", "market": "linear", "strategy": "frozen V1 signal rules; separate USDT-linear settlement", "start": a.start, "end": a.end, "symbols": list(a.symbols), "records": records, "funding": "excluded", "fee_rate_D": .0004, "slippage_bp_D": 2.0}
    (a.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(a.output), "symbols": len(a.symbols), "scenarios": 2, "status": "PASS"}))
    return 0
if __name__ == "__main__": raise SystemExit(main())
