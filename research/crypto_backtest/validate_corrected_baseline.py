"""Validate corrected COIN-M baseline accounting without changing signals."""
from __future__ import annotations
import argparse, json
from decimal import Decimal
from pathlib import Path
import numpy as np
import pandas as pd
from coin_m_engine import ContractSpec, fee_btc, fill_prices, pnl_btc

SPEC = ContractSpec()
TOL = 1e-12

def settle(row, fee_rate, slip_bp, exit_raw=None):
    side = row.direction
    raw_entry = float(row.raw_entry)
    raw_exit = float(exit_raw if exit_raw is not None else (row.sl if row.exit_reason == "SL" else row.tp))
    entry_fill, exit_fill = fill_prices(side, Decimal(str(raw_entry)), Decimal(str(raw_exit)), Decimal(str(slip_bp / 10000)))
    contracts = int(row.position_size)
    gross = float(pnl_btc(side, contracts, entry_fill, exit_fill, SPEC))
    raw = float(pnl_btc(side, contracts, Decimal(str(raw_entry)), Decimal(str(raw_exit)), SPEC))
    entry_fee = float(fee_btc(contracts, entry_fill, Decimal(str(fee_rate)), SPEC))
    exit_fee = float(fee_btc(contracts, exit_fill, Decimal(str(fee_rate)), SPEC))
    return {"gross_raw_btc": raw, "gross_btc": gross, "fee_btc": entry_fee + exit_fee,
            "slippage_btc": gross - raw, "net_btc": gross - entry_fee - exit_fee,
            "entry_fill": float(entry_fill), "exit_fill": float(exit_fill)}

def assert_ledger(rows, initial):
    assert np.isclose(rows.gross_btc.sum() - rows.gross_raw_btc.sum(), rows.slippage_btc.sum(), atol=TOL)
    assert np.isclose(rows.net_btc.sum(), rows.gross_btc.sum() - rows.fee_btc.sum(), atol=TOL)
    assert np.isclose(rows.equity_btc.iloc[-1], initial + rows.net_btc.sum(), atol=TOL)
    gp = rows.loc[rows.gross_btc > 0, "gross_btc"].sum(); gl = rows.loc[rows.gross_btc <= 0, "gross_btc"].sum()
    return {"gross_profit_btc": float(gp), "gross_loss_btc": float(gl), "gross_pnl_btc": float(rows.gross_btc.sum()),
            "net_pnl_btc": float(rows.net_btc.sum()), "profit_factor": float(gp / abs(gl)) if gl else None}

def metrics(rows):
    if rows.empty: return {"trades": 0}
    wins = rows.gross_btc > 0; gp = rows.loc[wins, "gross_btc"].sum(); gl = rows.loc[~wins, "gross_btc"].sum()
    dd = rows.equity_btc / rows.equity_btc.cummax() - 1
    return {"trades": len(rows), "win_rate": float(wins.mean()), "gross_profit_btc": float(gp), "gross_loss_btc": float(gl),
            "gross_pnl_btc": float(rows.gross_btc.sum()), "net_pnl_btc": float(rows.net_btc.sum()),
            "expectancy_price_r": float(rows.net_btc.div(rows.r_price_btc).mean()),
            "expectancy_all_in_r": float(rows.net_btc.div(rows.r_all_in_btc).mean()),
            "profit_factor": float(gp / abs(gl)) if gl else None, "max_drawdown": float(dd.min()),
            "initial_equity_btc": float(rows.initial_equity_btc.iloc[0]), "final_equity_btc": float(rows.equity_btc.iloc[-1])}

def independent_pnl(side, contracts, entry, exit_price):
    return contracts * 100 * ((1 / entry) - (1 / exit_price)) * (1 if side == "long" else -1)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--trades", type=Path, default=Path("reports/crypto-backtest/BASELINE_trades.csv")); ap.add_argument("--out", type=Path, default=Path("reports/crypto-backtest/corrected-validation")); args = ap.parse_args(); args.out.mkdir(parents=True, exist_ok=True)
    trades = pd.read_csv(args.trades, parse_dates=["entry_time", "exit_time"]).sort_values("entry_time").reset_index(drop=True); initial = float(trades.initial_equity_btc.iloc[0]); results = {}
    for name, fee, slip in [("A_0fee_0slip", 0, 0), ("B_004fee_0slip", .0004, 0), ("C_0fee_2bp", 0, 2), ("D_004fee_2bp", .0004, 2)]:
        out = []; equity = initial
        for _, row in trades.iterrows():
            actual = settle(row, fee, slip); stop = settle(row, fee, slip, exit_raw=row.sl)
            price_r = abs(float(pnl_btc(row.direction, int(row.position_size), Decimal(str(actual["entry_fill"])), Decimal(str(stop["exit_fill"])), SPEC))); all_in_r = abs(stop["net_btc"])
            equity_before = equity; equity += actual["net_btc"]
            out.append({**actual, "r_price_btc": price_r, "r_all_in_btc": all_in_r, "initial_equity_btc": equity_before, "equity_btc": equity, "direction": row.direction, "entry_time": row.entry_time, "exit_time": row.exit_time, "exit_reason": row.exit_reason})
        frame = pd.DataFrame(out); ledger = assert_ledger(frame, initial); frame.to_csv(args.out / f"{name}_trades.csv", index=False)
        results[name] = {**metrics(frame), "ledger": ledger, "initial_equity_usd": float(initial * float(trades.raw_entry.iloc[0])), "final_equity_usd_at_last_exit": float(frame.equity_btc.iloc[-1] * float(trades.exit.iloc[-1]))}
    sample = trades.sample(min(100, len(trades)), random_state=20260830); max_error = 0.0
    for _, row in sample.iterrows():
        entry = float(row.raw_entry); exit_price = float(row.sl if row.exit_reason == "SL" else row.tp); expected = independent_pnl(row.direction, int(row.position_size), entry, exit_price); actual = float(pnl_btc(row.direction, int(row.position_size), Decimal(str(entry)), Decimal(str(exit_price)), SPEC)); max_error = max(max_error, abs(expected - actual))
    old_path = Path("reports/crypto-backtest/diagnostics/diagnostic_summary.json"); old = json.loads(old_path.read_text(encoding="utf-8")) if old_path.exists() else None
    old_scenarios = old.get("cost_scenarios", {}) if old else {}
    old_comp = {name: {"old_legacy": old_scenarios.get(name), "corrected": value} for name, value in results.items()} if old else {}
    report = {"corrected_scenarios": results, "independent_cross_check": {"sample_size": len(sample), "max_abs_error": max_error, "pass": max_error <= TOL}, "old_vs_corrected": old_comp, "old_diagnostic_available": bool(old), "notes": ["All figures are BTC-base; USD is presentation conversion.", "Funding is unavailable and excluded, not assumed zero.", "Signals and EMA/ATR/entry rules were not changed."]}
    (args.out / "corrected_validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"); print(json.dumps(report, ensure_ascii=False))

if __name__ == "__main__": main()
