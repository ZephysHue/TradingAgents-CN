"""Reproducible baseline and parameter-matrix BTC futures backtest.

The engine is bar-close driven: signals on bar t fill at bar t+1 open.
Higher-timeframe bars are completed before their indicators are aligned.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import zipfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
from coin_m_engine import ContractSpec, fee_btc, fill_prices, pnl_btc, round_quantity, risk_per_contract_btc


KLINE_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
_MATRIX_BARS = None


def _init_matrix_worker(bars):
    global _MATRIX_BARS
    _MATRIX_BARS = bars


def _run_matrix_item(params):
    trades = run_strategy(_MATRIX_BARS, params)
    return summarize(trades, params)


@dataclass(frozen=True)
class Params:
    k: float = 0.2
    stop_atr: float = 0.3
    target_r: float = 2.0
    risk_pct: float = 0.01
    model: str = "A"
    zone_mode: str = "touch"
    slippage_bp: float = 2.0
    fee_rate: float = 0.0004
    daily_loss_r: float | None = 3.0
    daily_loss_streak: int | None = 3
    contract_size_usd: float = 100.0


def rma(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def settle_coin_m(side, contracts, entry_raw, exit_raw, params):
    spec = ContractSpec(contract_size_usd=Decimal("100"), min_qty=1, max_qty=60000, step_size=1)
    slip = Decimal(str(params.slippage_bp / 10000))
    entry_fill, exit_fill = fill_prices(side, Decimal(str(entry_raw)), Decimal(str(exit_raw)), slip)
    gross = pnl_btc(side, int(contracts), entry_fill, exit_fill, spec)
    entry_fee = fee_btc(int(contracts), entry_fill, Decimal(str(params.fee_rate)), spec)
    exit_fee = fee_btc(int(contracts), exit_fill, Decimal(str(params.fee_rate)), spec)
    raw_gross = pnl_btc(side, int(contracts), Decimal(str(entry_raw)), Decimal(str(exit_raw)), spec)
    return {"entry_fill": float(entry_fill), "exit_fill": float(exit_fill), "gross_pnl_btc": float(gross), "fee_btc": float(entry_fee + exit_fee), "slippage_cost_btc": float(gross - raw_gross), "net_pnl_btc": float(gross - entry_fee - exit_fee)}


def indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy().sort_index()
    out["ema20"] = out["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    out["ema50"] = out["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    prev = out["close"].shift(1)
    tr = pd.concat([out["high"] - out["low"], (out["high"] - prev).abs(), (out["low"] - prev).abs()], axis=1).max(axis=1)
    out["atr14"] = rma(tr, 14)
    out["swing_low"] = out["low"].where((out["low"] < out["low"].shift(1)) & (out["low"] < out["low"].shift(2)) & (out["low"] < out["low"].shift(-1)) & (out["low"] < out["low"].shift(-2)))
    out["swing_high"] = out["high"].where((out["high"] > out["high"].shift(1)) & (out["high"] > out["high"].shift(2)) & (out["high"] > out["high"].shift(-1)) & (out["high"] > out["high"].shift(-2)))
    # A swing at i is not usable until i+2 closes; shift the confirmed value back to the execution bar.
    out["confirmed_swing_low"] = out["swing_low"].shift(2).ffill()
    out["confirmed_swing_high"] = out["swing_high"].shift(2).ffill()
    return out


def read_data(data_dir: Path) -> tuple[pd.DataFrame, dict]:
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    frames = []
    for record in manifest["records"]:
        with zipfile.ZipFile(record["path"]) as archive:
            name = next(name for name in archive.namelist() if name.endswith(".csv"))
            frames.append(pd.read_csv(archive.open(name), header=None, names=KLINE_COLUMNS))
    frame = pd.concat(frames, ignore_index=True)
    frame["open_time"] = pd.to_numeric(frame["open_time"], errors="coerce")
    frame = frame.dropna(subset=["open_time"])
    frame["timestamp"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    frame = frame.drop_duplicates("timestamp").set_index("timestamp").sort_index()
    numeric = ["open", "high", "low", "close", "volume"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=numeric)
    frame = frame[(frame["high"] >= frame[["open", "close", "low"]].max(axis=1)) & (frame["low"] <= frame[["open", "close", "high"]].min(axis=1)) & (frame[numeric] > 0).all(axis=1)]
    return frame, manifest


def prepare(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    bars = {"15m": frame[["open", "high", "low", "close", "volume"]].resample("15min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(), "1h": frame.resample("1h", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(), "4h": frame.resample("4h", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()}
    return {key: indicators(value) for key, value in bars.items()}


def align_completed(high: pd.DataFrame, index: pd.DatetimeIndex, columns: list[str]) -> pd.DataFrame:
    # At a 15m bar timestamp, only higher-timeframe candles whose close time is before it are usable.
    usable = high.copy()
    usable.index = usable.index + pd.Timedelta(hours=1 if high.index.freq == pd.offsets.Hour(1) else 4)
    return usable[columns].reindex(index, method="ffill")


def run_strategy(bars: dict[str, pd.DataFrame], params: Params, initial_equity: float = 1000.0) -> pd.DataFrame:
    m15, h1, h4 = bars["15m"], bars["1h"], bars["4h"]
    # Resampled bars are labeled by their opening time. Shift to the first timestamp
    # at which the candle is complete, then align backward only to later 15m bars.
    h1_completed = h1.copy(); h1_completed.index = h1_completed.index + pd.Timedelta(hours=1)
    h4_completed = h4.copy(); h4_completed.index = h4_completed.index + pd.Timedelta(hours=4)
    h1a = h1_completed.reindex(m15.index, method="ffill")
    h4a = h4_completed.reindex(m15.index, method="ffill")
    work = m15.copy()
    work["h1_ema20"], work["h1_ema50"], work["h4_ema20"], work["h4_ema50"] = h1a["ema20"], h1a["ema50"], h4a["ema20"], h4a["ema50"]
    work["long_trend"] = (work.h4_ema20 > work.h4_ema50) & ((work.h1_close if "h1_close" in work else h1a.close) > work.h1_ema50) & (work.ema20 > work.ema50)
    work["short_trend"] = (work.h4_ema20 < work.h4_ema50) & ((work.h1_close if "h1_close" in work else h1a.close) < work.h1_ema50) & (work.ema20 < work.ema50)
    if params.model == "B":
        work["long_trend"] = (work.h4_ema20 > work.h4_ema50) & (h1a.ema20 > h1a.ema50) & (work.ema20 > work.ema50)
        work["short_trend"] = (work.h4_ema20 < work.h4_ema50) & (h1a.ema20 < h1a.ema50) & (work.ema20 < work.ema50)
    zone_low, zone_high = work.ema20 - params.k * work.atr14, work.ema20 + params.k * work.atr14
    touched = (work.low <= zone_high) & (work.high >= zone_low)
    inside = (work.close >= zone_low) & (work.close <= zone_high)
    was_above = work.close.shift(1) > work.ema20.shift(1)
    was_below = work.close.shift(1) < work.ema20.shift(1)
    long_signal = work.long_trend & (touched if params.zone_mode == "touch" else inside) & was_above & (work.close > work.high.shift(1)) & (work.close > work.ema20)
    short_signal = work.short_trend & (touched if params.zone_mode == "touch" else inside) & was_below & (work.close < work.low.shift(1)) & (work.close < work.ema20)
    trades = []
    rows = list(work.itertuples())
    h1_ema20_values = h1a["ema20"].to_numpy()
    h1_ema50_values = h1a["ema50"].to_numpy()
    long_signal_values = long_signal.to_numpy()
    short_signal_values = short_signal.to_numpy()
    open_values = work["open"].to_numpy()
    initial_price = float(m15["close"].iloc[0])
    equity = initial_equity / initial_price
    position = None
    loss_streak = 0
    day_r = 0.0
    current_day = None
    for i in range(2, len(work) - 1):
        ts, row = work.index[i], rows[i]
        if current_day != ts.date():
            current_day, day_r, loss_streak = ts.date(), 0.0, 0
        if position is not None:
            exit_price, reason = None, None
            if position["side"] == "long":
                if row.low <= position["sl"]: exit_price, reason = position["sl"], "SL"
                elif row.high >= position["tp"]: exit_price, reason = position["tp"], "TP"
            else:
                if row.high >= position["sl"]: exit_price, reason = position["sl"], "SL"
                elif row.low <= position["tp"]: exit_price, reason = position["tp"], "TP"
            if exit_price is not None:
                direction = 1 if position["side"] == "long" else -1
                settlement = settle_coin_m(position["side"], position["size"], position["entry_raw"], exit_price, params)
                actual_exit = settlement["exit_fill"]
                gross = settlement["gross_pnl_btc"]
                fee = settlement["fee_btc"]
                net = settlement["net_pnl_btc"]
                r_value = position["risk_amount"]
                pnl_r = net / r_value if r_value else 0
                equity += net; day_r += pnl_r; loss_streak = loss_streak + 1 if pnl_r < 0 else 0
                trades.append({**position["audit"], "exit_time": ts.isoformat(), "exit": actual_exit, "gross_pnl_btc": gross, "fee_btc": fee, "slippage_cost_btc": settlement["slippage_cost_btc"], "funding_btc": np.nan, "net_pnl_btc": net, "pnl_r": pnl_r, "exit_reason": reason, "final_equity_btc": equity, "final_equity": equity * actual_exit})
                position = None
                continue
        if position is None and ((params.daily_loss_r is None or day_r > -params.daily_loss_r) and (params.daily_loss_streak is None or loss_streak < params.daily_loss_streak)):
            side = "long" if long_signal_values[i] else "short" if short_signal_values[i] else None
            if side:
                entry_raw = float(open_values[i + 1])
                swing = row.confirmed_swing_low if side == "long" else row.confirmed_swing_high
                if not np.isfinite(swing) or not np.isfinite(row.atr14): continue
                sl = swing - params.stop_atr * row.atr14 if side == "long" else swing + params.stop_atr * row.atr14
                price_distance = entry_raw - sl if side == "long" else sl - entry_raw
                tp = entry_raw + params.target_r * price_distance if side == "long" else entry_raw - params.target_r * price_distance
                entry_decimal, stop_fill = fill_prices(side, Decimal(str(entry_raw)), Decimal(str(sl)), Decimal(str(params.slippage_bp / 10000)))
                entry = float(entry_decimal)
                risk_per_contract = float(risk_per_contract_btc(entry_decimal, stop_fill, ContractSpec()))
                if price_distance <= 0 or risk_per_contract <= 0: continue
                risk_amount = equity * params.risk_pct
                risk_size = risk_amount / risk_per_contract
                size = round_quantity(Decimal(str(risk_size)), ContractSpec())
                if size < 1: continue
                risk_amount = size * risk_per_contract
                position = {"side": side, "entry": entry, "entry_raw": entry_raw, "sl": sl, "tp": tp, "size": size, "risk_amount": risk_amount, "audit": {"entry_time": work.index[i+1].isoformat(), "direction": side, "raw_entry": entry_raw, "entry": entry, "sl": sl, "tp": tp, "position_size": size, "risk_btc": risk_amount, "initial_equity_btc": equity, "h4_ema20": row.h4_ema20, "h4_ema50": row.h4_ema50, "h1_ema20": h1_ema20_values[i], "h1_ema50": h1_ema50_values[i], "m15_ema20": row.ema20, "m15_ema50": row.ema50, "atr": row.atr14, "k": params.k, "stop_atr": params.stop_atr, "target_r": params.target_r, "swing_level": swing}}
    return pd.DataFrame(trades)


def summarize(trades: pd.DataFrame, params: Params) -> dict:
    if trades.empty: return {**asdict(params), "trades": 0}
    trades = trades.copy()
    trades["net_pnl"] = trades["net_pnl_btc"]
    trades["fee"] = trades["fee_btc"]
    trades["slippage"] = trades["slippage_cost_btc"]
    trades["final_equity"] = trades["final_equity_btc"]
    wins, losses = trades[trades.pnl_r > 0], trades[trades.pnl_r <= 0]
    gross_profit = trades.loc[trades.gross_pnl_btc > 0, "gross_pnl_btc"].sum(); gross_loss = abs(trades.loc[trades.gross_pnl_btc <= 0, "gross_pnl_btc"].sum())
    equity = trades.final_equity; peak = equity.cummax(); dd = equity / peak - 1
    signs = trades.pnl_r > 0
    runs = signs.ne(signs.shift()).cumsum()
    run_lengths = trades.groupby(runs).size()
    win_runs = trades.loc[signs].groupby(runs).size()
    loss_runs = trades.loc[~signs].groupby(runs).size()
    holds = pd.to_datetime(trades.exit_time) - pd.to_datetime(trades.entry_time)
    gross_loss_signed = trades.loc[trades.gross_pnl_btc <= 0, "gross_pnl_btc"].sum()
    return {**asdict(params), "accounting_unit": "BTC", "trades": len(trades), "long_trades": int((trades.direction == "long").sum()), "short_trades": int((trades.direction == "short").sum()), "win_rate": float(signs.mean()), "avg_win_r": float(wins.pnl_r.mean()) if len(wins) else 0, "avg_loss_r": float(abs(losses.pnl_r.mean())) if len(losses) else 0, "expectancy_r": float(trades.pnl_r.mean()), "profit_factor": float(gross_profit / gross_loss) if gross_loss else math.inf, "gross_profit_btc": float(gross_profit), "gross_loss_btc": float(gross_loss_signed), "gross_pnl_btc": float(trades.gross_pnl_btc.sum()), "net_pnl_btc": float(trades.net_pnl_btc.sum()), "fees_btc": float(trades.fee_btc.sum()), "slippage_cost_btc": float(trades.slippage_cost_btc.sum()), "funding_cost_btc": 0.0, "initial_equity_btc": float(trades.initial_equity_btc.iloc[0]), "max_drawdown": float(dd.min()), "max_drawdown_pct": float(dd.min()), "final_equity_btc": float(equity.iloc[-1]), "max_win_streak": int(win_runs.max()) if len(win_runs) else 0, "max_loss_streak": int(loss_runs.max()) if len(loss_runs) else 0, "avg_holding_hours": float(holds.dt.total_seconds().mean() / 3600) if len(holds) else 0, "max_holding_hours": float(holds.dt.total_seconds().max() / 3600) if len(holds) else 0, "monthly_trade_rate": float(len(trades) / max((pd.to_datetime(trades.exit_time).max() - pd.to_datetime(trades.entry_time).min()).days / 30.4375, 1))}


def assert_ledger(trades: pd.DataFrame, initial_equity_btc: float) -> None:
    if trades.empty:
        return
    gross = float(trades["gross_pnl_btc"].sum())
    fees = float(trades["fee_btc"].sum())
    net = float(trades["net_pnl_btc"].sum())
    assert math.isclose(gross - fees, net, rel_tol=1e-10, abs_tol=1e-12), "gross - fees != net"
    assert math.isclose(initial_equity_btc + net, float(trades["final_equity_btc"].iloc[-1]), rel_tol=1e-10, abs_tol=1e-12), "equity ledger mismatch"
    wins = trades.loc[trades["gross_pnl_btc"] > 0, "gross_pnl_btc"].sum()
    losses = trades.loc[trades["gross_pnl_btc"] <= 0, "gross_pnl_btc"].sum()
    expected_pf = float(wins / abs(losses))
    reported_pf = float(summarize(trades, Params())["profit_factor"])
    assert math.isclose(expected_pf, reported_pf, rel_tol=1e-10, abs_tol=1e-12), "profit factor check failed"


def slice_bars(bars: dict[str, pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    return {key: value.loc[(value.index >= start - pd.Timedelta(days=10)) & (value.index <= end)].copy() for key, value in bars.items()}


def monte_carlo(trades: pd.DataFrame, initial: float = 200.0, runs: int = 10000) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rng = np.random.default_rng(20260830)
    returns = trades.pnl_r.to_numpy()
    paths = np.empty((runs, len(returns)))
    for i in range(runs):
        paths[i] = rng.choice(returns, size=len(returns), replace=True)
    equity = initial * np.cumprod(1 + paths * 0.01, axis=1)
    return pd.DataFrame({"final_equity": equity[:, -1], "max_drawdown": (equity / np.maximum.accumulate(equity, axis=1) - 1).min(axis=1), "ruin_below_50": (equity.min(axis=1) <= 50), "reached_1000": (equity.max(axis=1) >= 1000)})


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--data-dir", type=Path, required=True); parser.add_argument("--output-dir", type=Path, required=True); parser.add_argument("--baseline-only", action="store_true"); args = parser.parse_args()
    frame, manifest = read_data(args.data_dir); bars = prepare(frame); args.output_dir.mkdir(parents=True, exist_ok=True)
    grid = [Params(k=k, stop_atr=s, target_r=t, risk_pct=r, model=model) for k in [0.1, 0.2, 0.3, 0.5] for s in [0.2, 0.3, 0.5] for t in [1.5, 2.0, 2.5, 3.0] for r in [0.005, 0.01, 0.02] for model in ["A", "B"]]
    summaries, baseline = [], None
    if args.baseline_only:
        baseline = run_strategy(bars, Params())
    else:
        workers = max(1, min(8, (os.cpu_count() or 2) - 1))
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_matrix_worker, initargs=(bars,)) as pool:
            summaries = list(pool.map(_run_matrix_item, grid, chunksize=4))
        baseline = run_strategy(bars, Params())
    assert_ledger(baseline, 1000.0 / float(bars["15m"]["close"].iloc[0]))
    pd.DataFrame(summaries).to_csv(args.output_dir / "all_parameter_combinations.csv", index=False)
    (baseline if baseline is not None else pd.DataFrame()).to_csv(args.output_dir / "BASELINE_trades.csv", index=False)
    split = bars["15m"].index[int(len(bars["15m"]) * 0.7)]
    oos = run_strategy(slice_bars(bars, split, bars["15m"].index[-1]), Params())
    mc = monte_carlo(baseline)
    report = {"data_manifest": manifest, "data_rows_15m": len(bars["15m"]), "baseline": summarize(baseline, Params()), "out_of_sample_70_30": summarize(oos, Params()), "monte_carlo_10000": {"status": "paused_by_request"}, "notes": ["Funding history is not included until a verified funding archive is supplied; funding is reported as unavailable, not zero.", "This runner uses conservative SL-first handling for same-bar SL/TP; 1m sequencing is not available in this dataset.", "COIN-M correction is active: BTC-denominated inverse settlement, integer contract quantity, and directional slippage. Optimization and paused experiments are not run in this validation."]}
    (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output": str(args.output_dir), "baseline_trades": len(baseline), "parameter_combinations": len(grid), "market": manifest["selected_market"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__": raise SystemExit(main())
