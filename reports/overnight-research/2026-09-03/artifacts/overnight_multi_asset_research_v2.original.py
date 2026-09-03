"""Frozen offline multi-asset overnight research executor v2."""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

INITIAL_EQUITY = 1000.0
SYMBOL_EXPOSURE_PCT = 0.10
PORTFOLIO_EXPOSURE_PCT = 0.80
BASELINE_FEE_RATE = 0.0004
BASELINE_SLIPPAGE = 0.0002
STRESS_MULTIPLIER = 1.5
WARNING_FLAGS = [
    "exploratory_survivorship_bias_present",
    "funding_unmodeled_not_deployable",
]
MODEL_HEALTH_URL = "http://127.0.0.1:1234/health"
MODEL_MODELS_URL = "http://127.0.0.1:1234/v1/models"
MODEL_CHAT_URL = "http://127.0.0.1:1234/v1/chat/completions"
STAGES: dict[str, tuple[str, str]] = {
    "development": ("2026-06-01 00:00:00+00:00", "2026-06-30 23:45:00+00:00"),
    "validation": ("2026-07-01 00:00:00+00:00", "2026-07-31 23:45:00+00:00"),
    "holdout": ("2026-08-01 00:00:00+00:00", "2026-08-29 23:45:00+00:00"),
}
STAGE_INDEX: dict[str, pd.DatetimeIndex] = {
    stage: pd.date_range(start, end, freq="15min", tz="UTC")
    for stage, (start, end) in STAGES.items()
}
FAMILY_NAMES = {
    "A": "mean_reversion_bollinger_rsi",
    "B": "trend_pullback_hourly_filter",
    "C": "tiered_cross_sectional_momentum",
    "D": "donchian_atr_breakout",
}
RUN_MODE_TEXT = "paper-only / 本地模型 / 固定 Bybit 15m 缓存 / 1000 USDT / 不自动晋级"
RESULT_METRIC_COLUMNS = [
    "family",
    "family_name",
    "param_id",
    "params_json",
    "stage",
    "trade_count",
    "win_rate",
    "net_expectancy_bps",
    "profit_factor",
    "net_pnl",
    "ending_equity",
    "max_drawdown_pct",
    "avg_holding_bars",
    "max_holding_bars",
    "unresolved_positions",
    "terminal_mtm_pnl",
    "max_gross_exposure_pct",
    "drawdown_start_utc",
    "drawdown_trough_utc",
    "recovery_utc",
    "duration_15m_bars",
]


def utc_now() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_float(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out


def _shanghai_now() -> str:
    return str(pd.Timestamp.now(tz="Asia/Shanghai"))


def _sorted_result_frame(rows: list[dict[str, Any]], columns: list[str], sort_by: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(rows)
    for column in columns:
        if column not in frame.columns:
            frame[column] = pd.NA
    available_sort = [column for column in sort_by if column in frame.columns]
    if available_sort:
        frame = frame.sort_values(available_sort)
    return frame.loc[:, columns].copy()


def _metric_brief(row: dict[str, Any]) -> str:
    trade_count = int(row.get("trade_count", 0) or 0)
    expectancy = _safe_float(row.get("net_expectancy_bps"))
    profit_factor = _safe_float(row.get("profit_factor"))
    return f"trades={trade_count}, exp={expectancy:.2f}bp, pf={profit_factor:.2f}"


def write_running_summary(
    summary_path: Path,
    output_dir: Path,
    status: str,
    started_at_utc: str,
    notes: list[str],
    chosen_rows: dict[str, dict[str, Any]] | None = None,
    validation_rows: list[dict[str, Any]] | None = None,
    holdout_rows: list[dict[str, Any]] | None = None,
    candidate_registry: list[dict[str, Any]] | None = None,
    rejection_registry: list[dict[str, Any]] | None = None,
) -> None:
    chosen_rows = chosen_rows or {}
    validation_rows = validation_rows or []
    holdout_rows = holdout_rows or []
    candidate_registry = candidate_registry or []
    rejection_registry = rejection_registry or []
    validation_by_family = _family_rows_by_name(validation_rows)
    holdout_by_family = _family_rows_by_name(holdout_rows)
    lines = [
        "# 夜间多资产量化研究 v2 晨报",
        "",
        f"- 启动时间：{started_at_utc}",
        f"- 最近写入：{_shanghai_now()}",
        f"- 状态：{status}",
        f"- 模式：{RUN_MODE_TEXT}",
        f"- 输出目录：`{output_dir}`",
        "",
        "## 当前进度",
        "",
    ]
    lines.extend([f"{index}. {note}" for index, note in enumerate(notes, start=1)])
    if chosen_rows:
        lines += [
            "",
            "## Development 已选参数",
            "",
            "| 家族 | 参数 | Development | Validation | Holdout |",
            "|---|---|---|---|---|",
        ]
        for family in ("A", "B", "C", "D"):
            selected = chosen_rows.get(family)
            if selected is None:
                lines.append(f"| {family} | not_selected | not_run | not_run | not_run |")
                continue
            validation = validation_by_family.get(family)
            holdout = holdout_by_family.get(family)
            validation_text = (
                f"{validation.get('status')} / {_metric_brief(validation)}"
                if validation
                else "pending"
            )
            holdout_text = (
                f"{holdout.get('status')} / {_metric_brief(holdout)}"
                if holdout
                else "pending"
            )
            lines.append(
                f"| {family} | `{selected['param_id']}` | {_metric_brief(selected)} | {validation_text} | {holdout_text} |"
            )
    lines += [
        "",
        "## 候选/拒绝计数",
        "",
        f"- challenger：{len(candidate_registry)}",
        f"- rejected：{len(rejection_registry)}",
    ]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fill_price(raw_open: float, side: str, is_entry: bool, slippage: float) -> float:
    if side == "long":
        return raw_open * (1 + slippage if is_entry else 1 - slippage)
    return raw_open * (1 - slippage if is_entry else 1 + slippage)


def can_open_position(equity: float, current_exposure: float, proposed_notional: float) -> bool:
    if not np.isfinite(equity) or equity <= 0 or proposed_notional <= 0:
        return False
    return current_exposure + proposed_notional <= equity * PORTFOLIO_EXPOSURE_PCT + 1e-9


def validation_gate_reasons(metrics: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if int(metrics.get("trade_count", 0)) < 150:
        reasons.append("validation_trade_count_lt_150")
    if _safe_float(metrics.get("win_rate")) <= 50:
        reasons.append("validation_win_rate_le_50")
    if _safe_float(metrics.get("net_expectancy_bps")) <= 0:
        reasons.append("validation_expectancy_le_0")
    if _safe_float(metrics.get("profit_factor")) <= 1.05:
        reasons.append("validation_profit_factor_le_1.05")
    if _safe_float(metrics.get("max_drawdown_pct")) < -25:
        reasons.append("validation_drawdown_lt_-25")
    return reasons


def holdout_classification(metrics: dict[str, Any], stress_metrics: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if int(metrics.get("trade_count", 0)) < 300:
        reasons.append("holdout_trade_count_lt_300")
    if _safe_float(metrics.get("win_rate")) <= 50:
        reasons.append("holdout_win_rate_le_50")
    if _safe_float(metrics.get("net_expectancy_bps")) <= 0:
        reasons.append("holdout_expectancy_le_0")
    if _safe_float(metrics.get("profit_factor")) <= 1.10:
        reasons.append("holdout_profit_factor_le_1.10")
    if _safe_float(metrics.get("max_drawdown_pct")) < -25:
        reasons.append("holdout_drawdown_lt_-25")
    if _safe_float(stress_metrics.get("net_expectancy_bps")) <= 0:
        reasons.append("stress_expectancy_le_0")
    if _safe_float(stress_metrics.get("profit_factor")) <= 1.00:
        reasons.append("stress_profit_factor_le_1.00")
    if reasons:
        return "rejected_holdout", reasons
    return "exploratory_challenger", []


def build_parameter_budget() -> dict[str, list[dict[str, Any]]]:
    family_a = [
        {
            "family": "A",
            "param_id": f"A{index:02d}",
            "bb_window": bb_window,
            "bb_width": bb_width,
            "rsi_length": rsi_length,
            "rsi_entry": rsi_entry,
            "max_hold_bars": 12,
        }
        for index, (bb_window, bb_width, rsi_length, rsi_entry) in enumerate(
            itertools.product((18, 20, 24), (1.8, 2.0), (7, 14), (20, 25)),
            start=1,
        )
    ]
    family_b = [
        {
            "family": "B",
            "param_id": f"B{index:02d}",
            "trend_fast": trend_fast,
            "trend_slow": trend_slow,
            "pullback_ema": pullback_ema,
            "rsi_length": rsi_length,
            "rsi_floor": rsi_floor,
            "take_profit_atr": 1.5,
            "max_hold_bars": 16,
        }
        for index, (trend_fast, trend_slow, pullback_ema, rsi_length, rsi_floor) in enumerate(
            itertools.product((36, 50), (120, 200), (16, 20), (10, 14), (50, 55)),
            start=1,
        )
    ]
    family_c = [
        {
            "family": "C",
            "param_id": f"C{index:02d}",
            "lookback_hours": lookback_hours,
            "selection_pct": selection_pct,
            "hold_hours": hold_hours,
            "rebalance_hours": rebalance_hours,
        }
        for index, (lookback_hours, selection_pct, hold_hours, rebalance_hours) in enumerate(
            itertools.product((12, 24, 48), (0.2, 0.3), (4, 8), (1, 4)),
            start=1,
        )
    ]
    family_d = [
        {
            "family": "D",
            "param_id": f"D{index:02d}",
            "donchian_window": donchian_window,
            "breakout_buffer_atr": breakout_buffer_atr,
            "atr_ratio_threshold": atr_ratio_threshold,
            "max_hold_bars": max_hold_bars,
            "take_profit_atr": 2.0,
        }
        for index, (donchian_window, breakout_buffer_atr, atr_ratio_threshold, max_hold_bars) in enumerate(
            itertools.product((20, 40), (0.0, 0.1), (1.0, 1.2), (12, 20)),
            start=1,
        )
    ]
    budget = {"A": family_a, "B": family_b, "C": family_c, "D": family_d}
    return budget


def parameter_budget_document(selected_params: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    selected_params = selected_params or {}
    families = build_parameter_budget()
    total = sum(len(rows) for rows in families.values())
    return {
        "generated_at_utc": utc_now(),
        "warning_flags": WARNING_FLAGS,
        "total_parameter_sets": total,
        "total_cap": 192,
        "family_cap": 48,
        "families": [
            {
                "family": family,
                "family_name": FAMILY_NAMES[family],
                "parameter_sets": len(rows),
                "selected_param_id": selected_params.get(family, {}).get("param_id"),
                "selected_params": selected_params.get(family),
            }
            for family, rows in families.items()
        ],
    }


def _rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50.0)


def _prepare_symbol_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = frame.copy()
    base["timestamp"] = pd.to_datetime(base["timestamp"], utc=True)
    base = base.sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")
    for column in ("open", "high", "low", "close", "volume"):
        if column in base.columns:
            base[column] = pd.to_numeric(base[column], errors="coerce")
    tr = pd.concat(
        [
            base["high"] - base["low"],
            (base["high"] - base["close"].shift()).abs(),
            (base["low"] - base["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    base["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    for length in (7, 10, 14):
        base[f"rsi_{length}"] = _rsi(base["close"], length)
    for span in (16, 20):
        base[f"ema_{span}"] = base["close"].ewm(span=span, adjust=False).mean()
    for window in (18, 20, 24):
        base[f"bb_mid_{window}"] = base["close"].rolling(window).mean()
        base[f"bb_std_{window}"] = base["close"].rolling(window).std(ddof=0)
    for window in (20, 40):
        base[f"donchian_high_{window}"] = base["high"].shift(1).rolling(window).max()
        base[f"donchian_low_{window}"] = base["low"].shift(1).rolling(window).min()
    base["atr_median_96"] = base["atr14"].shift(1).rolling(96).median()

    hourly = (
        base[["open", "close"]]
        .resample("1h", label="left", closed="left")
        .agg(open=("open", "first"), close=("close", "last"))
        .dropna()
    )
    for span in (36, 50, 120, 200):
        hourly[f"ema_{span}"] = hourly["close"].ewm(span=span, adjust=False).mean()
    for lookback in (12, 24, 48):
        hourly[f"ret_{lookback}h"] = hourly["close"] / hourly["close"].shift(lookback) - 1

    base = base.copy()
    base["hour_key"] = base.index.floor("1h") - pd.Timedelta(hours=1)
    hour_cols = [f"ema_{span}" for span in (36, 50, 120, 200)] + [f"ret_{lookback}h" for lookback in (12, 24, 48)]
    base = base.merge(hourly[hour_cols], left_on="hour_key", right_index=True, how="left")
    base = base.drop(columns="hour_key")
    base["timestamp"] = base.index
    base["mark_close"] = base["close"].ffill()
    base["mark_open"] = base["open"].where(base["open"].notna(), base["close"].ffill())
    return base, hourly


def load_frozen_data(manifest_path: Path, universe_path: Path) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    selected_symbols = {row["symbol"] for row in universe["selected"]}
    stage_frames: dict[str, pd.DataFrame] = {}
    hourly_frames: dict[str, pd.DataFrame] = {}
    inventory: list[dict[str, Any]] = []
    root = Path.cwd()
    for item in manifest["files"]:
        symbol = item["symbol"]
        if symbol not in selected_symbols:
            continue
        path = root / item["path"].replace("\\", "/")
        if not path.exists():
            continue
        raw = pd.read_csv(path, compression="gzip")
        prepared, hourly = _prepare_symbol_frame(raw)
        stage_frames[symbol] = prepared
        hourly_frames[symbol] = hourly
        inventory.append(
            {
                "symbol": symbol,
                "path": str(path),
                "sha256": item.get("sha256", sha256_file(path)),
                "rows": int(len(prepared)),
                "utc_start": str(prepared.index.min()),
                "utc_end": str(prepared.index.max()),
            }
        )
    return stage_frames, hourly_frames, manifest, universe, inventory


def stage_members(universe: dict[str, Any], available_symbols: set[str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {stage: [] for stage in STAGES}
    for row in universe["selected"]:
        if row["symbol"] in available_symbols and row["stage"] in out:
            out[row["stage"]].append(row)
    for stage in out:
        out[stage] = sorted(out[stage], key=lambda item: (item["tier"], item["rank"], item["symbol"]))
    return out


def stage_view_for_symbol(base: pd.DataFrame, stage: str) -> pd.DataFrame:
    view = base.reindex(STAGE_INDEX[stage]).copy()
    view["mark_close"] = view["close"].ffill()
    view["mark_open"] = view["open"].where(view["open"].notna(), view["mark_close"])
    return view


def build_stage_views(stage_frames: dict[str, pd.DataFrame]) -> dict[str, dict[str, pd.DataFrame]]:
    return {
        symbol: {stage: stage_view_for_symbol(base, stage) for stage in STAGES}
        for symbol, base in stage_frames.items()
    }


def market_stage_summary(stage: str, members: list[dict[str, Any]], stage_views: dict[str, dict[str, pd.DataFrame]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"stage": stage, "symbols": len(members), "tiers": {}}
    for tier in ("hot", "mid", "low"):
        tier_members = [member for member in members if member["tier"] == tier]
        returns: list[float] = []
        realized_vol: list[float] = []
        coverage: list[float] = []
        for member in tier_members:
            view = stage_views[member["symbol"]][stage]
            usable = view["close"].dropna()
            if usable.empty:
                continue
            returns.append(float(usable.iloc[-1] / usable.iloc[0] - 1))
            realized_vol.append(float(usable.pct_change().abs().median()))
            coverage.append(float(len(usable) / len(view) * 100))
        summary["tiers"][tier] = {
            "symbols": len(tier_members),
            "median_period_return_pct": None if not returns else float(np.median(returns) * 100),
            "median_abs_15m_return_pct": None if not realized_vol else float(np.median(realized_vol) * 100),
            "median_coverage_pct": None if not coverage else float(np.median(coverage)),
        }
    return summary


def _mark_price(view: pd.DataFrame, ts: pd.Timestamp, field: str, fallback: float) -> float:
    if ts not in view.index:
        return fallback
    for candidate in (field, "close", "mark_open", "mark_close"):
        value = view.at[ts, candidate] if candidate in view.columns else np.nan
        if np.isfinite(value):
            return float(value)
    return fallback


def _current_equity(
    cash: float,
    positions: dict[str, dict[str, Any]],
    stage_views: dict[str, dict[str, pd.DataFrame]],
    stage: str,
    ts: pd.Timestamp,
    field: str,
) -> float:
    unrealized = 0.0
    for position in positions.values():
        view = stage_views[position["symbol"]][stage]
        mark = _mark_price(view, ts, field, position["entry_fill_price"])
        direction = 1 if position["side"] == "long" else -1
        unrealized += position["notional"] * direction * (mark / position["entry_fill_price"] - 1)
    return cash + unrealized


def _close_trade_row(
    position: dict[str, Any],
    stage_views: dict[str, dict[str, pd.DataFrame]],
    stage: str,
    ts: pd.Timestamp,
    fee_rate: float,
    slippage: float,
    reason: str,
) -> dict[str, Any]:
    view = stage_views[position["symbol"]][stage]
    raw_open = _mark_price(view, ts, "open", position["entry_fill_price"])
    exit_fill = fill_price(raw_open, position["side"], False, slippage)
    direction = 1 if position["side"] == "long" else -1
    gross_pnl = position["notional"] * direction * (exit_fill / position["entry_fill_price"] - 1)
    exit_fee = position["notional"] * fee_rate
    net_pnl = gross_pnl - position["entry_fee"] - exit_fee
    return {
        "trade_id": position["trade_id"],
        "family": position["family"],
        "family_name": FAMILY_NAMES[position["family"]],
        "param_id": position["param_id"],
        "params_json": _json_dumps(position["params"]),
        "stage": stage,
        "symbol": position["symbol"],
        "tier": position["tier"],
        "side": position["side"],
        "signal_timestamp_utc": str(position["signal_timestamp_utc"]),
        "entry_timestamp_utc": str(position["entry_timestamp_utc"]),
        "exit_timestamp_utc": str(ts),
        "entry_raw_open": position["entry_raw_open"],
        "entry_fill_price": position["entry_fill_price"],
        "exit_raw_open": raw_open,
        "exit_fill_price": exit_fill,
        "notional": position["notional"],
        "entry_fee": position["entry_fee"],
        "exit_fee": exit_fee,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "gross_return_bps": gross_pnl / position["notional"] * 1e4,
        "net_return_bps": net_pnl / position["notional"] * 1e4,
        "holding_bars": int(position["bars"]),
        "exit_reason": reason,
        "funding_unmodeled": True,
    }


def _unresolved_position_row(
    position: dict[str, Any],
    stage_views: dict[str, dict[str, pd.DataFrame]],
    stage: str,
) -> dict[str, Any]:
    last_ts = STAGE_INDEX[stage][-1]
    view = stage_views[position["symbol"]][stage]
    last_close = _mark_price(view, last_ts, "close", position["entry_fill_price"])
    direction = 1 if position["side"] == "long" else -1
    mtm_pnl = position["notional"] * direction * (last_close / position["entry_fill_price"] - 1) - position["entry_fee"]
    return {
        "trade_id": position["trade_id"],
        "family": position["family"],
        "family_name": FAMILY_NAMES[position["family"]],
        "param_id": position["param_id"],
        "params_json": _json_dumps(position["params"]),
        "stage": stage,
        "symbol": position["symbol"],
        "tier": position["tier"],
        "side": position["side"],
        "signal_timestamp_utc": str(position["signal_timestamp_utc"]),
        "entry_timestamp_utc": str(position["entry_timestamp_utc"]),
        "exit_timestamp_utc": None,
        "entry_raw_open": position["entry_raw_open"],
        "entry_fill_price": position["entry_fill_price"],
        "stage_end_mark_close": last_close,
        "notional": position["notional"],
        "entry_fee": position["entry_fee"],
        "holding_bars": int(position["bars"]),
        "terminal_mtm_pnl": mtm_pnl,
        "terminal_status": "unclosed_no_t_plus_1_open",
        "funding_unmodeled": True,
    }


def _entry_signal_a(row: pd.Series, params: dict[str, Any]) -> tuple[str, float] | None:
    atr = _safe_float(row.get("atr14"))
    mid = _safe_float(row.get(f"bb_mid_{params['bb_window']}"))
    std = _safe_float(row.get(f"bb_std_{params['bb_window']}"))
    rsi = _safe_float(row.get(f"rsi_{params['rsi_length']}"))
    close = _safe_float(row.get("close"))
    if not all(np.isfinite(value) for value in (atr, mid, std, rsi, close)) or atr <= 0 or std <= 0:
        return None
    low_band = mid - params["bb_width"] * std
    high_band = mid + params["bb_width"] * std
    if close < low_band and rsi < params["rsi_entry"]:
        return "long", abs(low_band - close) / atr
    if close > high_band and rsi > 100 - params["rsi_entry"]:
        return "short", abs(close - high_band) / atr
    return None


def _entry_signal_b(row: pd.Series, params: dict[str, Any]) -> tuple[str, float] | None:
    atr = _safe_float(row.get("atr14"))
    fast = _safe_float(row.get(f"ema_{params['pullback_ema']}"))
    slow_fast = _safe_float(row.get(f"ema_{params['pullback_ema']}"))
    trend_fast = _safe_float(row.get(f"ema_{params['trend_fast']}"))
    trend_slow = _safe_float(row.get(f"ema_{params['trend_slow']}"))
    hourly_fast = _safe_float(row.get(f"ema_{params['trend_fast']}"), default=float("nan"))
    hourly_slow = _safe_float(row.get(f"ema_{params['trend_slow']}"), default=float("nan"))
    # merge() places hourly EMA columns without prefix; use the hourly values here.
    hourly_fast = _safe_float(row.get(f"ema_{params['trend_fast']}"))
    hourly_slow = _safe_float(row.get(f"ema_{params['trend_slow']}"))
    pullback = _safe_float(row.get(f"ema_{params['pullback_ema']}"))
    rsi = _safe_float(row.get(f"rsi_{params['rsi_length']}"))
    low = _safe_float(row.get("low"))
    high = _safe_float(row.get("high"))
    close = _safe_float(row.get("close"))
    if not all(np.isfinite(value) for value in (atr, pullback, rsi, low, high, close, hourly_fast, hourly_slow)) or atr <= 0:
        return None
    if hourly_fast > hourly_slow and low <= pullback and close > pullback and rsi >= params["rsi_floor"]:
        return "long", abs(close - pullback) / atr
    if hourly_fast < hourly_slow and high >= pullback and close < pullback and rsi <= 100 - params["rsi_floor"]:
        return "short", abs(close - pullback) / atr
    return None


def _entry_signal_d(row: pd.Series, params: dict[str, Any]) -> tuple[str, float] | None:
    atr = _safe_float(row.get("atr14"))
    atr_med = _safe_float(row.get("atr_median_96"))
    high = _safe_float(row.get(f"donchian_high_{params['donchian_window']}"))
    low = _safe_float(row.get(f"donchian_low_{params['donchian_window']}"))
    close = _safe_float(row.get("close"))
    if not all(np.isfinite(value) for value in (atr, atr_med, high, low, close)) or atr <= 0 or atr_med <= 0:
        return None
    atr_ratio = atr / atr_med
    if atr_ratio < params["atr_ratio_threshold"]:
        return None
    long_trigger = high + params["breakout_buffer_atr"] * atr
    short_trigger = low - params["breakout_buffer_atr"] * atr
    if close > long_trigger:
        return "long", abs(close - long_trigger) / atr
    if close < short_trigger:
        return "short", abs(close - short_trigger) / atr
    return None


def _entry_signal(family: str, row: pd.Series, params: dict[str, Any]) -> tuple[str, float] | None:
    if family == "A":
        return _entry_signal_a(row, params)
    if family == "B":
        return _entry_signal_b(row, params)
    if family == "D":
        return _entry_signal_d(row, params)
    raise ValueError(f"unsupported family {family}")


def _exit_reason(family: str, row: pd.Series, position: dict[str, Any], params: dict[str, Any]) -> str | None:
    close = _safe_float(row.get("close"))
    if not np.isfinite(close):
        return None
    if family == "A":
        mid = _safe_float(row.get(f"bb_mid_{params['bb_window']}"))
        if not np.isfinite(mid):
            return None
        if position["bars"] >= params["max_hold_bars"]:
            return "time_exit"
        if position["side"] == "long":
            if close >= mid:
                return "mid_revert_exit"
            if close <= position["entry_fill_price"] - position["atr_signal"]:
                return "atr_stop_exit"
        else:
            if close <= mid:
                return "mid_revert_exit"
            if close >= position["entry_fill_price"] + position["atr_signal"]:
                return "atr_stop_exit"
        return None
    if family == "B":
        if position["bars"] >= params["max_hold_bars"]:
            return "time_exit"
        if position["side"] == "long":
            if close <= position["entry_fill_price"] - position["atr_signal"]:
                return "atr_stop_exit"
            if close >= position["entry_fill_price"] + params["take_profit_atr"] * position["atr_signal"]:
                return "take_profit_exit"
        else:
            if close >= position["entry_fill_price"] + position["atr_signal"]:
                return "atr_stop_exit"
            if close <= position["entry_fill_price"] - params["take_profit_atr"] * position["atr_signal"]:
                return "take_profit_exit"
        return None
    if family == "D":
        if position["bars"] >= params["max_hold_bars"]:
            return "time_exit"
        if position["side"] == "long":
            if close <= position["entry_fill_price"] - position["atr_signal"]:
                return "atr_stop_exit"
            if close >= position["entry_fill_price"] + params["take_profit_atr"] * position["atr_signal"]:
                return "take_profit_exit"
        else:
            if close >= position["entry_fill_price"] + position["atr_signal"]:
                return "atr_stop_exit"
            if close <= position["entry_fill_price"] - params["take_profit_atr"] * position["atr_signal"]:
                return "take_profit_exit"
        return None
    raise ValueError(f"unsupported family {family}")


def run_signal_family(
    family: str,
    params: dict[str, Any],
    stage: str,
    members: list[dict[str, Any]],
    stage_views: dict[str, dict[str, pd.DataFrame]],
    fee_rate: float = BASELINE_FEE_RATE,
    slippage: float = BASELINE_SLIPPAGE,
) -> dict[str, Any]:
    timeline = STAGE_INDEX[stage]
    cash = INITIAL_EQUITY
    positions: dict[str, dict[str, Any]] = {}
    pending_entries: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    pending_exits: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    closed: list[dict[str, Any]] = []
    audit = {
        "family": family,
        "param_id": params["param_id"],
        "stage": stage,
        "created_signals": 0,
        "filled_entries": 0,
        "filled_exits": 0,
        "portfolio_limit_blocks": 0,
        "same_symbol_overlap_blocks": 0,
        "missing_execution_bars": 0,
    }
    trade_id = 1
    members_by_symbol = {member["symbol"]: member for member in members}
    for index, ts in enumerate(timeline):
        for exit_order in pending_exits.pop(ts, []):
            symbol = exit_order["symbol"]
            position = positions.get(symbol)
            if position is None:
                continue
            view = stage_views[symbol][stage]
            if ts not in view.index or not np.isfinite(view.at[ts, "open"]):
                audit["missing_execution_bars"] += 1
                continue
            row = _close_trade_row(position, stage_views, stage, ts, fee_rate, slippage, exit_order["reason"])
            cash += row["gross_pnl"] - row["exit_fee"]
            closed.append(row)
            audit["filled_exits"] += 1
            positions.pop(symbol, None)

        if ts in pending_entries:
            ordered_entries = sorted(
                pending_entries.pop(ts),
                key=lambda row: (-row["signal_score"], row["symbol"], row["side"]),
            )
            for entry_order in ordered_entries:
                symbol = entry_order["symbol"]
                if symbol in positions:
                    audit["same_symbol_overlap_blocks"] += 1
                    continue
                view = stage_views[symbol][stage]
                if ts not in view.index or not np.isfinite(view.at[ts, "open"]):
                    audit["missing_execution_bars"] += 1
                    continue
                equity = _current_equity(cash, positions, stage_views, stage, ts, "open")
                current_exposure = sum(position["notional"] for position in positions.values())
                proposed_notional = round(equity * SYMBOL_EXPOSURE_PCT, 8)
                if not can_open_position(equity, current_exposure, proposed_notional):
                    audit["portfolio_limit_blocks"] += 1
                    continue
                raw_open = float(view.at[ts, "open"])
                entry_fill = fill_price(raw_open, entry_order["side"], True, slippage)
                entry_fee = proposed_notional * fee_rate
                cash -= entry_fee
                positions[symbol] = {
                    "trade_id": trade_id,
                    "family": family,
                    "param_id": params["param_id"],
                    "params": params,
                    "symbol": symbol,
                    "tier": entry_order["tier"],
                    "side": entry_order["side"],
                    "signal_timestamp_utc": entry_order["signal_timestamp_utc"],
                    "entry_timestamp_utc": ts,
                    "entry_raw_open": raw_open,
                    "entry_fill_price": entry_fill,
                    "entry_fee": entry_fee,
                    "notional": proposed_notional,
                    "atr_signal": entry_order["atr_signal"],
                    "bars": 0,
                }
                trade_id += 1
                audit["filled_entries"] += 1

        if index >= len(timeline) - 1:
            continue
        next_ts = timeline[index + 1]
        for symbol, position in list(positions.items()):
            view = stage_views[symbol][stage]
            row = view.loc[ts]
            if not np.isfinite(_safe_float(row.get("close"))):
                continue
            position["bars"] += 1
            reason = _exit_reason(family, row, position, params)
            if reason:
                pending_exits.setdefault(next_ts, []).append({"symbol": symbol, "reason": reason})

        for member in members:
            symbol = member["symbol"]
            if symbol in positions:
                continue
            view = stage_views[symbol][stage]
            row = view.loc[ts]
            signal = _entry_signal(family, row, params)
            if signal is None:
                continue
            side, score = signal
            pending_entries.setdefault(next_ts, []).append(
                {
                    "symbol": symbol,
                    "tier": member["tier"],
                    "side": side,
                    "signal_score": score,
                    "signal_timestamp_utc": ts,
                    "atr_signal": _safe_float(row.get("atr14")),
                }
            )
            audit["created_signals"] += 1

    unresolved = [
        _unresolved_position_row(position, stage_views, stage)
        for position in positions.values()
    ]
    audit["unresolved_open_positions"] = len(unresolved)
    audit["unresolved_mtm_pnl"] = float(sum(row["terminal_mtm_pnl"] for row in unresolved))
    return {"closed_trades": closed, "unresolved_positions": unresolved, "audit": audit}


def run_cross_sectional_family(
    params: dict[str, Any],
    stage: str,
    members: list[dict[str, Any]],
    stage_views: dict[str, dict[str, pd.DataFrame]],
    hourly_views: dict[str, pd.DataFrame],
    fee_rate: float = BASELINE_FEE_RATE,
    slippage: float = BASELINE_SLIPPAGE,
) -> dict[str, Any]:
    start, end = map(pd.Timestamp, STAGES[stage])
    execution_times = pd.date_range(start, end, freq=f"{params['rebalance_hours']}h", tz="UTC")
    cash = INITIAL_EQUITY
    positions: dict[str, dict[str, Any]] = {}
    closed: list[dict[str, Any]] = []
    audit = {
        "family": "C",
        "param_id": params["param_id"],
        "stage": stage,
        "created_signals": 0,
        "filled_entries": 0,
        "filled_exits": 0,
        "portfolio_limit_blocks": 0,
        "same_symbol_overlap_blocks": 0,
        "missing_execution_bars": 0,
        "late_stage_blocks": 0,
    }
    trade_id = 1
    tier_groups = {
        tier: [member for member in members if member["tier"] == tier]
        for tier in ("hot", "mid", "low")
    }
    hold_delta = pd.Timedelta(hours=params["hold_hours"])
    for ts in execution_times:
        for symbol, position in list(positions.items()):
            if position["exit_at"] != ts:
                continue
            view = stage_views[symbol][stage]
            if ts not in view.index or not np.isfinite(view.at[ts, "open"]):
                audit["missing_execution_bars"] += 1
                continue
            row = _close_trade_row(position, stage_views, stage, ts, fee_rate, slippage, "timed_rotation_exit")
            cash += row["gross_pnl"] - row["exit_fee"]
            closed.append(row)
            positions.pop(symbol, None)
            audit["filled_exits"] += 1

        ranked_orders: list[dict[str, Any]] = []
        signal_hour = ts - pd.Timedelta(hours=1)
        for tier, members_in_tier in tier_groups.items():
            ranked: list[tuple[float, str]] = []
            for member in members_in_tier:
                hourly = hourly_views.get(member["symbol"])
                if hourly is None or signal_hour not in hourly.index:
                    continue
                score = _safe_float(hourly.at[signal_hour, f"ret_{params['lookback_hours']}h"])
                if not np.isfinite(score):
                    continue
                ranked.append((score, member["symbol"]))
            if not ranked:
                continue
            ranked.sort(key=lambda item: (-item[0], item[1]))
            count = max(1, int(math.floor(len(ranked) * params["selection_pct"])))
            long_side = [(score, symbol, "long") for score, symbol in ranked[:count]]
            short_side = [(score, symbol, "short") for score, symbol in ranked[-count:]]
            for score, symbol, side in long_side + short_side:
                ranked_orders.append({"symbol": symbol, "tier": tier, "side": side, "signal_score": abs(score), "signal_timestamp_utc": signal_hour})
                audit["created_signals"] += 1

        for order in sorted(ranked_orders, key=lambda item: (-item["signal_score"], item["symbol"], item["side"])):
            symbol = order["symbol"]
            if symbol in positions:
                audit["same_symbol_overlap_blocks"] += 1
                continue
            exit_at = ts + hold_delta
            if exit_at > end:
                audit["late_stage_blocks"] += 1
                continue
            view = stage_views[symbol][stage]
            if ts not in view.index or not np.isfinite(view.at[ts, "open"]):
                audit["missing_execution_bars"] += 1
                continue
            equity = _current_equity(cash, positions, stage_views, stage, ts, "open")
            current_exposure = sum(position["notional"] for position in positions.values())
            proposed_notional = round(equity * SYMBOL_EXPOSURE_PCT, 8)
            if not can_open_position(equity, current_exposure, proposed_notional):
                audit["portfolio_limit_blocks"] += 1
                continue
            raw_open = float(view.at[ts, "open"])
            entry_fill = fill_price(raw_open, order["side"], True, slippage)
            entry_fee = proposed_notional * fee_rate
            cash -= entry_fee
            positions[symbol] = {
                "trade_id": trade_id,
                "family": "C",
                "param_id": params["param_id"],
                "params": params,
                "symbol": symbol,
                "tier": order["tier"],
                "side": order["side"],
                "signal_timestamp_utc": order["signal_timestamp_utc"],
                "entry_timestamp_utc": ts,
                "entry_raw_open": raw_open,
                "entry_fill_price": entry_fill,
                "entry_fee": entry_fee,
                "notional": proposed_notional,
                "atr_signal": 0.0,
                "bars": params["hold_hours"] * 4,
                "exit_at": exit_at,
            }
            trade_id += 1
            audit["filled_entries"] += 1

    unresolved = [
        _unresolved_position_row(position, stage_views, stage)
        for position in positions.values()
    ]
    audit["unresolved_open_positions"] = len(unresolved)
    audit["unresolved_mtm_pnl"] = float(sum(row["terminal_mtm_pnl"] for row in unresolved))
    return {"closed_trades": closed, "unresolved_positions": unresolved, "audit": audit}


def build_equity_curve(
    stage: str,
    closed_trades: list[dict[str, Any]],
    unresolved_positions: list[dict[str, Any]],
    stage_views: dict[str, dict[str, pd.DataFrame]],
) -> pd.DataFrame:
    timeline = STAGE_INDEX[stage]
    cash = INITIAL_EQUITY
    active: dict[int, dict[str, Any]] = {}
    all_rows = closed_trades + unresolved_positions
    entries_by_ts: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    exits_by_ts: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    for row in all_rows:
        entry_ts = pd.Timestamp(row["entry_timestamp_utc"])
        entries_by_ts.setdefault(entry_ts, []).append(row)
        exit_raw = row.get("exit_timestamp_utc")
        if exit_raw:
            exits_by_ts.setdefault(pd.Timestamp(exit_raw), []).append(row)
    curve_rows: list[dict[str, Any]] = []
    for ts in timeline:
        for row in entries_by_ts.get(ts, []):
            cash -= _safe_float(row["entry_fee"], 0.0)
            active[int(row["trade_id"])] = row
        for row in exits_by_ts.get(ts, []):
            trade_id = int(row["trade_id"])
            if trade_id in active:
                cash += _safe_float(row["gross_pnl"], 0.0) - _safe_float(row["exit_fee"], 0.0)
                active.pop(trade_id, None)
        unrealized = 0.0
        gross_exposure = 0.0
        for row in active.values():
            symbol = row["symbol"]
            mark = _mark_price(stage_views[symbol][stage], ts, "close", _safe_float(row["entry_fill_price"], 0.0))
            direction = 1 if row["side"] == "long" else -1
            notional = _safe_float(row["notional"], 0.0)
            gross_exposure += notional
            unrealized += notional * direction * (mark / _safe_float(row["entry_fill_price"], 1.0) - 1)
        curve_rows.append(
            {
                "timestamp_utc": str(ts),
                "equity": cash + unrealized,
                "realized_component": cash - INITIAL_EQUITY,
                "unrealized_component": unrealized,
                "gross_exposure": gross_exposure,
                "active_positions": len(active),
            }
        )
    return pd.DataFrame(curve_rows)


def drawdown_summary(curve: pd.DataFrame) -> dict[str, Any]:
    if curve.empty:
        return {
            "max_drawdown_pct": 0.0,
            "drawdown_start_utc": None,
            "drawdown_trough_utc": None,
            "recovery_utc": None,
            "duration_15m_bars": 0,
        }
    equity = curve["equity"]
    peak = equity.cummax()
    drawdown = equity / peak - 1
    trough = int(drawdown.idxmin())
    start = int(equity.iloc[: trough + 1].idxmax())
    recovery = next((index for index in range(trough + 1, len(curve)) if equity.iloc[index] >= equity.iloc[start]), None)
    final = len(curve) - 1 if recovery is None else recovery
    return {
        "max_drawdown_pct": float(drawdown.iloc[trough] * 100),
        "drawdown_start_utc": curve.iloc[start]["timestamp_utc"],
        "drawdown_trough_utc": curve.iloc[trough]["timestamp_utc"],
        "recovery_utc": None if recovery is None else curve.iloc[recovery]["timestamp_utc"],
        "duration_15m_bars": int(final - start),
    }


def metrics_for_run(
    stage: str,
    family: str,
    params: dict[str, Any],
    closed_trades: list[dict[str, Any]],
    unresolved_positions: list[dict[str, Any]],
    stage_views: dict[str, dict[str, pd.DataFrame]],
) -> tuple[dict[str, Any], pd.DataFrame]:
    curve = build_equity_curve(stage, closed_trades, unresolved_positions, stage_views)
    dd = drawdown_summary(curve)
    ledger = pd.DataFrame(closed_trades)
    if ledger.empty:
        metrics = {
            "family": family,
            "family_name": FAMILY_NAMES[family],
            "param_id": params["param_id"],
            "params_json": _json_dumps(params),
            "stage": stage,
            "trade_count": 0,
            "win_rate": float("nan"),
            "net_expectancy_bps": float("nan"),
            "profit_factor": float("nan"),
            "net_pnl": 0.0,
            "ending_equity": float(curve.iloc[-1]["equity"]) if not curve.empty else INITIAL_EQUITY,
            "max_drawdown_pct": dd["max_drawdown_pct"],
            "avg_holding_bars": float("nan"),
            "max_holding_bars": float("nan"),
            "unresolved_positions": len(unresolved_positions),
            "terminal_mtm_pnl": float(sum(row["terminal_mtm_pnl"] for row in unresolved_positions)),
            "max_gross_exposure_pct": float(curve["gross_exposure"].max() / max(curve["equity"].max(), 1e-9) * 100) if not curve.empty else 0.0,
            **dd,
        }
        return metrics, curve

    pnl = ledger["net_pnl"]
    positives = pnl[pnl > 0].sum()
    negatives = pnl[pnl < 0].sum()
    profit_factor = float(positives / abs(negatives)) if negatives < 0 else float("inf")
    metrics = {
        "family": family,
        "family_name": FAMILY_NAMES[family],
        "param_id": params["param_id"],
        "params_json": _json_dumps(params),
        "stage": stage,
        "trade_count": int(len(ledger)),
        "win_rate": float((pnl > 0).mean() * 100),
        "net_expectancy_bps": float(ledger["net_return_bps"].mean()),
        "profit_factor": profit_factor,
        "net_pnl": float(pnl.sum()),
        "ending_equity": float(curve.iloc[-1]["equity"]) if not curve.empty else float(INITIAL_EQUITY + pnl.sum()),
        "max_drawdown_pct": dd["max_drawdown_pct"],
        "avg_holding_bars": float(ledger["holding_bars"].mean()),
        "max_holding_bars": float(ledger["holding_bars"].max()),
        "unresolved_positions": len(unresolved_positions),
        "terminal_mtm_pnl": float(sum(row["terminal_mtm_pnl"] for row in unresolved_positions)),
        "max_gross_exposure_pct": float(curve["gross_exposure"].max() / max(curve["equity"].max(), 1e-9) * 100) if not curve.empty else 0.0,
        **dd,
    }
    return metrics, curve


def stress_metrics_from_holdout(
    stage: str,
    family: str,
    params: dict[str, Any],
    closed_trades: list[dict[str, Any]],
    stage_views: dict[str, dict[str, pd.DataFrame]],
) -> dict[str, Any]:
    if not closed_trades:
        return {
            "family": family,
            "family_name": FAMILY_NAMES[family],
            "param_id": params["param_id"],
            "stage": stage,
            "trade_count": 0,
            "win_rate": float("nan"),
            "net_expectancy_bps": float("nan"),
            "profit_factor": float("nan"),
            "net_pnl": 0.0,
            "status": "not_run_due_to_no_holdout_trades",
        }
    fee_rate = BASELINE_FEE_RATE * STRESS_MULTIPLIER
    slippage = BASELINE_SLIPPAGE * STRESS_MULTIPLIER
    stressed_rows: list[dict[str, Any]] = []
    for row in closed_trades:
        entry_fill = fill_price(_safe_float(row["entry_raw_open"]), row["side"], True, slippage)
        exit_fill = fill_price(_safe_float(row["exit_raw_open"]), row["side"], False, slippage)
        direction = 1 if row["side"] == "long" else -1
        notional = _safe_float(row["notional"])
        entry_fee = notional * fee_rate
        exit_fee = notional * fee_rate
        gross_pnl = notional * direction * (exit_fill / entry_fill - 1)
        net_pnl = gross_pnl - entry_fee - exit_fee
        stressed_rows.append({**row, "entry_fill_price": entry_fill, "exit_fill_price": exit_fill, "entry_fee": entry_fee, "exit_fee": exit_fee, "gross_pnl": gross_pnl, "net_pnl": net_pnl, "net_return_bps": net_pnl / notional * 1e4})
    metrics, _ = metrics_for_run(stage, family, params, stressed_rows, [], stage_views)
    metrics["status"] = "computed"
    return metrics


def _selection_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float, float]:
    eligible = 1.0 if int(row.get("trade_count", 0)) >= 100 else 0.0
    expectancy = _safe_float(row.get("net_expectancy_bps"), -1e12)
    profit_factor = _safe_float(row.get("profit_factor"), -1e12)
    net_pnl = _safe_float(row.get("net_pnl"), -1e12)
    trade_count = float(int(row.get("trade_count", 0)))
    return eligible, expectancy, profit_factor, net_pnl, trade_count


def select_best_development_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for family in ("A", "B", "C", "D"):
        family_rows = [row for row in rows if row["family"] == family]
        selected[family] = sorted(
            family_rows,
            key=lambda row: (_selection_sort_key(row), row["param_id"]),
            reverse=True,
        )[0]
    return selected


def _run_family(
    family: str,
    params: dict[str, Any],
    stage: str,
    members: list[dict[str, Any]],
    stage_views: dict[str, dict[str, pd.DataFrame]],
    hourly_views: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    if family == "C":
        return run_cross_sectional_family(params, stage, members, stage_views, hourly_views)
    return run_signal_family(family, params, stage, members, stage_views)


def run_overnight_research(
    manifest_path: Path,
    universe_path: Path,
    output_dir: Path,
    summary_path: Path,
) -> dict[str, Any]:
    started_at_utc = _shanghai_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    stage_frames, hourly_views, manifest, universe, inventory = load_frozen_data(manifest_path, universe_path)
    stage_views = build_stage_views(stage_frames)
    members_by_stage = stage_members(universe, set(stage_frames))
    parameter_budget = build_parameter_budget()

    development_rows: list[dict[str, Any]] = []
    selected_traces: dict[tuple[str, str], dict[str, Any]] = {}
    selected_params: dict[str, dict[str, Any]] = {}
    all_curves: list[pd.DataFrame] = []
    drawdown_rows: list[dict[str, Any]] = []
    ledgers_to_persist: list[tuple[str, str, list[dict[str, Any]], list[dict[str, Any]]]] = []

    market_summary = {
        stage: market_stage_summary(stage, members, stage_views)
        for stage, members in members_by_stage.items()
    }
    write_running_summary(
        summary_path=summary_path,
        output_dir=output_dir,
        status="running_development",
        started_at_utc=started_at_utc,
        notes=[
            "已读取冻结输入 manifest / universe，并锁定 paper-only / 1000U / 80% 组合上限。",
            "正在运行 Development 全参数扫描（A=24, B=32, C=24, D=16）。",
            "晨报会在选出 Development 参数、完成 Validation/Holdout 后继续覆盖更新。",
        ],
    )

    for family in ("A", "B", "C", "D"):
        for params in parameter_budget[family]:
            result = _run_family(family, params, "development", members_by_stage["development"], stage_views, hourly_views)
            metrics, curve = metrics_for_run("development", family, params, result["closed_trades"], result["unresolved_positions"], stage_views)
            metrics["status"] = "development_ranked"
            metrics["audit_json"] = _json_dumps(result["audit"])
            development_rows.append(metrics)
            selected_traces[(family, params["param_id"])] = {
                "params": params,
                "result": result,
                "metrics": metrics,
                "curve": curve,
            }

    chosen_rows = select_best_development_rows(development_rows)
    write_running_summary(
        summary_path=summary_path,
        output_dir=output_dir,
        status="running_validation_holdout",
        started_at_utc=started_at_utc,
        notes=[
            "Development 全参数扫描完成，已为四个策略族各自锁定 1 组 Validation 候选。",
            "正在依次执行 Validation gate；通过者才进入 Holdout 与 1.5x 成本压力测试。",
            "若所有家族都在 Validation 被拒，晨报将明确落盘 empty-holdout 结果而不是报错退出。",
        ],
        chosen_rows=chosen_rows,
    )
    validation_rows: list[dict[str, Any]] = []
    holdout_rows: list[dict[str, Any]] = []
    stress_rows: list[dict[str, Any]] = []
    candidate_registry: list[dict[str, Any]] = []
    rejection_registry: list[dict[str, Any]] = []

    for family in ("A", "B", "C", "D"):
        selected_metric = chosen_rows[family]
        selected_metric["selected_for_validation"] = True
        trace = selected_traces[(family, selected_metric["param_id"])]
        params = trace["params"]
        selected_params[family] = params

        dev_curve = trace["curve"].copy()
        dev_curve.insert(0, "family", family)
        dev_curve.insert(1, "param_id", params["param_id"])
        dev_curve.insert(2, "stage", "development")
        all_curves.append(dev_curve)
        drawdown_rows.append({"family": family, "param_id": params["param_id"], "stage": "development", **drawdown_summary(dev_curve)})
        ledgers_to_persist.append((family, "development", trace["result"]["closed_trades"], trace["result"]["unresolved_positions"]))

        validation_result = _run_family(family, params, "validation", members_by_stage["validation"], stage_views, hourly_views)
        validation_metrics, validation_curve = metrics_for_run("validation", family, params, validation_result["closed_trades"], validation_result["unresolved_positions"], stage_views)
        validation_reasons = validation_gate_reasons(validation_metrics)
        validation_metrics["status"] = "validation_pass" if not validation_reasons else "rejected_in_validation"
        validation_metrics["gate_reasons"] = ";".join(validation_reasons)
        validation_metrics["audit_json"] = _json_dumps(validation_result["audit"])
        validation_rows.append(validation_metrics)

        validation_curve = validation_curve.copy()
        validation_curve.insert(0, "family", family)
        validation_curve.insert(1, "param_id", params["param_id"])
        validation_curve.insert(2, "stage", "validation")
        all_curves.append(validation_curve)
        drawdown_rows.append({"family": family, "param_id": params["param_id"], "stage": "validation", **drawdown_summary(validation_curve)})
        ledgers_to_persist.append((family, "validation", validation_result["closed_trades"], validation_result["unresolved_positions"]))

        if validation_reasons:
            rejection_registry.append(
                {
                    "family": family,
                    "family_name": FAMILY_NAMES[family],
                    "param_id": params["param_id"],
                    "stage": "validation",
                    "classification": "rejected_in_validation",
                    "reasons": validation_reasons,
                    "metrics": validation_metrics,
                    "warning_flags": WARNING_FLAGS,
                }
            )
            write_running_summary(
                summary_path=summary_path,
                output_dir=output_dir,
                status=f"running_after_{family}_validation_reject",
                started_at_utc=started_at_utc,
                notes=[
                    f"家族 {family} 已完成 Validation，并被 gate 拒绝。",
                    "未通过 Validation 的家族不会进入 Holdout。",
                    "其余家族继续执行；晨报保留当前累计拒绝/候选计数。",
                ],
                chosen_rows=chosen_rows,
                validation_rows=validation_rows,
                holdout_rows=holdout_rows,
                candidate_registry=candidate_registry,
                rejection_registry=rejection_registry,
            )
            continue

        holdout_result = _run_family(family, params, "holdout", members_by_stage["holdout"], stage_views, hourly_views)
        holdout_metrics, holdout_curve = metrics_for_run("holdout", family, params, holdout_result["closed_trades"], holdout_result["unresolved_positions"], stage_views)
        stress_metrics = stress_metrics_from_holdout("holdout", family, params, holdout_result["closed_trades"], stage_views)
        classification, reasons = holdout_classification(holdout_metrics, stress_metrics)
        holdout_metrics["status"] = classification
        holdout_metrics["gate_reasons"] = ";".join(reasons)
        holdout_metrics["audit_json"] = _json_dumps(holdout_result["audit"])
        holdout_rows.append(holdout_metrics)
        stress_metrics["classification"] = classification
        stress_metrics["gate_reasons"] = ";".join(reasons)
        stress_rows.append(stress_metrics)

        holdout_curve = holdout_curve.copy()
        holdout_curve.insert(0, "family", family)
        holdout_curve.insert(1, "param_id", params["param_id"])
        holdout_curve.insert(2, "stage", "holdout")
        all_curves.append(holdout_curve)
        drawdown_rows.append({"family": family, "param_id": params["param_id"], "stage": "holdout", **drawdown_summary(holdout_curve)})
        ledgers_to_persist.append((family, "holdout", holdout_result["closed_trades"], holdout_result["unresolved_positions"]))

        registry_row = {
            "family": family,
            "family_name": FAMILY_NAMES[family],
            "param_id": params["param_id"],
            "params": params,
            "classification": classification,
            "metrics": holdout_metrics,
            "cost_stress_metrics": stress_metrics,
            "warning_flags": WARNING_FLAGS,
        }
        if classification == "exploratory_challenger":
            candidate_registry.append(registry_row)
        else:
            rejection_registry.append({**registry_row, "stage": "holdout", "reasons": reasons})
        write_running_summary(
            summary_path=summary_path,
            output_dir=output_dir,
            status=f"running_after_{family}_holdout",
            started_at_utc=started_at_utc,
            notes=[
                f"家族 {family} 已完成 Validation、Holdout 与 1.5x 成本压力测试。",
                "当前晨报已写入累计 Validation/Holdout 状态与候选/拒绝计数。",
                "最终晨报仍会补齐完整指标、ledger、equity curve 与模型原始分析状态。",
            ],
            chosen_rows=chosen_rows,
            validation_rows=validation_rows,
            holdout_rows=holdout_rows,
            candidate_registry=candidate_registry,
            rejection_registry=rejection_registry,
        )

    development_df = _sorted_result_frame(
        development_rows,
        columns=RESULT_METRIC_COLUMNS + ["status", "audit_json", "selected_for_validation"],
        sort_by=["family", "param_id"],
    )
    development_df["selected_for_validation"] = development_df["param_id"].isin({row["param_id"] for row in chosen_rows.values()})
    validation_df = _sorted_result_frame(
        validation_rows,
        columns=RESULT_METRIC_COLUMNS + ["status", "gate_reasons", "audit_json"],
        sort_by=["family"],
    )
    holdout_df = _sorted_result_frame(
        holdout_rows,
        columns=RESULT_METRIC_COLUMNS + ["status", "gate_reasons", "audit_json"],
        sort_by=["family"],
    )
    stress_df = _sorted_result_frame(
        stress_rows,
        columns=RESULT_METRIC_COLUMNS + ["status", "classification", "gate_reasons"],
        sort_by=["family"],
    )

    development_df.to_csv(output_dir / "development_results.csv", index=False)
    validation_df.to_csv(output_dir / "validation_results.csv", index=False)
    holdout_df.to_csv(output_dir / "holdout_results.csv", index=False)
    stress_df.to_csv(output_dir / "cost_stress_results.csv", index=False)
    (output_dir / "candidate_registry.json").write_text(json.dumps(candidate_registry, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (output_dir / "rejection_registry.json").write_text(json.dumps(rejection_registry, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    ledgers_dir = output_dir / "trade_ledgers"
    ledgers_dir.mkdir(parents=True, exist_ok=True)
    for family, stage, closed_trades, unresolved_positions in ledgers_to_persist:
        ledger_path = ledgers_dir / f"{family.lower()}_{stage}_ledger.csv"
        unresolved_path = ledgers_dir / f"{family.lower()}_{stage}_unresolved.csv"
        pd.DataFrame(closed_trades).to_csv(ledger_path, index=False)
        pd.DataFrame(unresolved_positions).to_csv(unresolved_path, index=False)

    if all_curves:
        pd.concat(all_curves, ignore_index=True).to_csv(output_dir / "equity_curves.csv", index=False)
    else:
        pd.DataFrame(columns=["family", "param_id", "stage", "timestamp_utc", "equity", "realized_component", "unrealized_component", "gross_exposure", "active_positions"]).to_csv(output_dir / "equity_curves.csv", index=False)
    pd.DataFrame(drawdown_rows).to_csv(output_dir / "drawdown_summary.csv", index=False)

    protocol = {
        "generated_at_utc": utc_now(),
        "warning_flags": WARNING_FLAGS,
        "inputs": {
            "data_manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
            "universe_manifest": {"path": str(universe_path), "sha256": sha256_file(universe_path)},
        },
        "capital": {
            "initial_equity_usdt": INITIAL_EQUITY,
            "symbol_exposure_pct": SYMBOL_EXPOSURE_PCT,
            "portfolio_exposure_pct": PORTFOLIO_EXPOSURE_PCT,
            "leverage": 0.0,
        },
        "costs": {
            "baseline_fee_rate": BASELINE_FEE_RATE,
            "baseline_slippage": BASELINE_SLIPPAGE,
            "stress_multiplier": STRESS_MULTIPLIER,
        },
        "stages": {
            stage: {
                "start_utc": bounds[0],
                "end_utc": bounds[1],
                "members": len(members_by_stage[stage]),
                "market_summary": market_summary[stage],
            }
            for stage, bounds in STAGES.items()
        },
        "selection_rule": "Development rank by trade_count>=100 first, then net_expectancy_bps, profit_factor, net_pnl, trade_count.",
        "validation_gate": {
            "trade_count_min": 150,
            "win_rate_gt": 50,
            "net_expectancy_bps_gt": 0,
            "profit_factor_gt": 1.05,
            "max_drawdown_pct_gte": -25,
        },
        "holdout_gate": {
            "trade_count_min": 300,
            "win_rate_gt": 50,
            "net_expectancy_bps_gt": 0,
            "profit_factor_gt": 1.10,
            "max_drawdown_pct_gte": -25,
            "stress_net_expectancy_bps_gt": 0,
            "stress_profit_factor_gt": 1.00,
        },
        "parameter_budget": parameter_budget_document(selected_params),
        "data_inventory": inventory,
    }
    (output_dir / "research_protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "data_manifest_copy.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "parameter_budget.json").write_text(json.dumps(parameter_budget_document(selected_params), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    model_analysis = run_local_model_analysis(chosen_rows, market_summary["development"])
    (output_dir / "model_analysis_raw.json").write_text(json.dumps(model_analysis, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    write_morning_summary(
        summary_path=summary_path,
        output_dir=output_dir,
        started_at_utc=started_at_utc,
        chosen_rows=chosen_rows,
        validation_rows=validation_rows,
        holdout_rows=holdout_rows,
        stress_rows=stress_rows,
        candidate_registry=candidate_registry,
        rejection_registry=rejection_registry,
        market_summary=market_summary,
        model_analysis=model_analysis,
    )

    return {
        "market_summary": market_summary,
        "development_rows": development_rows,
        "validation_rows": validation_rows,
        "holdout_rows": holdout_rows,
        "stress_rows": stress_rows,
        "candidate_registry": candidate_registry,
        "rejection_registry": rejection_registry,
        "chosen_rows": chosen_rows,
        "selected_params": selected_params,
    }


def run_local_model_analysis(chosen_rows: dict[str, dict[str, Any]], development_market_summary: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    out: dict[str, Any] = {
        "generated_at_utc": utc_now(),
        "health_url": MODEL_HEALTH_URL,
        "models_url": MODEL_MODELS_URL,
        "chat_url": MODEL_CHAT_URL,
        "warning_flags": WARNING_FLAGS,
        "development_market_summary": development_market_summary,
        "selected_development_rows": chosen_rows,
    }
    try:
        health_response = requests.get(MODEL_HEALTH_URL, timeout=5)
        out["health_status_code"] = health_response.status_code
        out["health_body"] = health_response.json()
    except Exception as exc:  # pragma: no cover - network failure path
        out["health_error"] = repr(exc)
        out["elapsed_seconds"] = round(time.time() - started, 3)
        return out
    try:
        models_response = requests.get(MODEL_MODELS_URL, timeout=5)
        out["models_status_code"] = models_response.status_code
        models_payload = models_response.json()
        out["models_body"] = models_payload
        model_id = models_payload["data"][0]["id"]
    except Exception as exc:  # pragma: no cover - network failure path
        out["models_error"] = repr(exc)
        out["elapsed_seconds"] = round(time.time() - started, 3)
        return out

    request_payload = {
        "model": model_id,
        "temperature": 0.2,
        "max_tokens": 400,
        "messages": [
            {
                "role": "system",
                "content": "You are an offline quant research analyst. Do not claim profitability. Summarize only structure, failure modes, and audit focus in JSON.",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "Analyze frozen development-stage multi-asset research outputs. Stay descriptive and skeptical.",
                        "development_market_summary": development_market_summary,
                        "selected_families": {
                            family: {
                                "param_id": row["param_id"],
                                "params_json": row["params_json"],
                                "trade_count": row["trade_count"],
                                "win_rate": row["win_rate"],
                                "net_expectancy_bps": row["net_expectancy_bps"],
                                "profit_factor": row["profit_factor"],
                                "max_drawdown_pct": row["max_drawdown_pct"],
                            }
                            for family, row in chosen_rows.items()
                        },
                        "required_schema": {
                            "overall_market_structure": "string",
                            "family_fit": [{"family": "A|B|C|D", "why_it_fits": "string", "failure_mode": "string"}],
                            "audit_focus": ["string"],
                            "forbidden": ["profit claims", "deployment approval"],
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    out["request_payload"] = request_payload
    try:
        chat_response = requests.post(MODEL_CHAT_URL, json=request_payload, timeout=180)
        out["chat_status_code"] = chat_response.status_code
        try:
            out["chat_body"] = chat_response.json()
        except Exception:
            out["chat_body_text"] = chat_response.text
    except Exception as exc:  # pragma: no cover - network failure path
        out["chat_error"] = repr(exc)
    out["elapsed_seconds"] = round(time.time() - started, 3)
    return out


def _family_rows_by_name(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["family"]: row for row in rows}


def write_morning_summary(
    summary_path: Path,
    output_dir: Path,
    started_at_utc: str,
    chosen_rows: dict[str, dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    holdout_rows: list[dict[str, Any]],
    stress_rows: list[dict[str, Any]],
    candidate_registry: list[dict[str, Any]],
    rejection_registry: list[dict[str, Any]],
    market_summary: dict[str, Any],
    model_analysis: dict[str, Any],
) -> None:
    validation_by_family = _family_rows_by_name(validation_rows)
    holdout_by_family = _family_rows_by_name(holdout_rows)
    stress_by_family = _family_rows_by_name(stress_rows)
    lines = [
        "# 夜间多资产量化研究 v2 晨报",
        "",
        f"- 启动时间：{started_at_utc}",
        f"- 完成时间：{_shanghai_now()}",
        "- 状态：completed",
        "- 运行次数：1",
        f"- 模式：{RUN_MODE_TEXT}",
        f"- 输出目录：`{output_dir}`",
        "",
        "## 冻结输入摘要",
        "",
        "- Development: 2026-06-01..2026-06-30",
        "- Validation: 2026-07-01..2026-07-31",
        "- Holdout: 2026-08-01..2026-08-29",
        "- 单标的 10% / 组合 80% / 无杠杆 / Funding 标记为未建模不可部署",
        "- 参数预算：A=24, B=32, C=24, D=16, 总计=96 <= 192",
        "",
        "## Development 市场结构摘要",
        "",
        "```json",
        json.dumps(market_summary["development"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 本地模型原始分析状态",
        "",
        f"- /health: {model_analysis.get('health_status_code')} {model_analysis.get('health_body')}",
        f"- /v1/models: {model_analysis.get('models_status_code')}",
        f"- /v1/chat/completions: {model_analysis.get('chat_status_code', model_analysis.get('chat_error', 'not_run'))}",
        "",
        "## 各策略族结果",
        "",
        "| 家族 | Development 选中参数 | Development 指标 | Validation | Holdout | 成本压力 |",
        "|---|---|---|---|---|---|",
    ]
    for family in ("A", "B", "C", "D"):
        dev = chosen_rows[family]
        validation = validation_by_family.get(family, {})
        holdout = holdout_by_family.get(family, {})
        stress = stress_by_family.get(family, {})
        dev_metrics = f"trades={dev['trade_count']}, wr={_safe_float(dev['win_rate']):.2f}, exp={_safe_float(dev['net_expectancy_bps']):.2f}bp, pf={_safe_float(dev['profit_factor']):.2f}"
        validation_metrics = (
            f"{validation.get('status')} / trades={validation.get('trade_count', 0)}, exp={_safe_float(validation.get('net_expectancy_bps')):.2f}bp, pf={_safe_float(validation.get('profit_factor')):.2f}"
            if validation
            else "not_run"
        )
        holdout_metrics = (
            f"{holdout.get('status')} / trades={holdout.get('trade_count', 0)}, exp={_safe_float(holdout.get('net_expectancy_bps')):.2f}bp, pf={_safe_float(holdout.get('profit_factor')):.2f}, dd={_safe_float(holdout.get('max_drawdown_pct')):.2f}%"
            if holdout
            else "not_run"
        )
        stress_metrics = (
            f"exp={_safe_float(stress.get('net_expectancy_bps')):.2f}bp, pf={_safe_float(stress.get('profit_factor')):.2f}"
            if stress
            else "not_run"
        )
        lines.append(
            f"| {family} | `{dev['param_id']}` {dev['params_json']} | {dev_metrics} | {validation_metrics} | {holdout_metrics} | {stress_metrics} |"
        )
    lines += [
        "",
        "## 候选与拒绝",
        "",
        f"- exploratory challenger 数量：{len(candidate_registry)}",
        f"- reject 数量：{len(rejection_registry)}",
    ]
    for row in candidate_registry:
        lines.append(f"- 候选 `{row['family']}` / `{row['param_id']}`：Holdout 通过，仍仅为 exploratory challenger。")
    for row in rejection_registry:
        reasons = row.get("reasons") or row.get("metrics", {}).get("gate_reasons", "")
        lines.append(f"- 拒绝 `{row['family']}` / `{row['param_id']}` @ {row.get('stage', 'holdout')}：{reasons}")
    lines += [
        "",
        "## 1000U 权益曲线摘要",
        "",
        "- 详见 `equity_curves.csv` 与 `drawdown_summary.csv`；所有阶段独立从 1000 USDT 起算。",
        "- 未在 Holdout 通过的家族不会进入 Paper Registry，也不会自动晋级。",
        "",
        "## 风险与限制",
        "",
        "- Funding 未建模：`funding_unmodeled_not_deployable`。",
        "- 当前宇宙来自冻结月度名单，仍存在幸存者偏差标记：`exploratory_survivorship_bias_present`。",
        "- 任何 challenger 仅属 exploratory，未接入实盘或自动 champion 流程。",
        "",
        "## 10 分钟人工审计清单",
        "",
        "1. 打开 `parameter_budget.json`，确认四族参数数分别为 24/32/24/16，总计 96。",
        "2. 抽查 `trade_ledgers/*_development_ledger.csv`，确认 notional 从 1000U 的 10% 起算。",
        "3. 打开 `drawdown_summary.csv`，确认任何候选 Holdout 最大回撤未低于 -25%。",
        "4. 对照 `cost_stress_results.csv`，确认候选在 1.5x 成本下仍为正期望。",
        "5. 查看 `model_analysis_raw.json`，确认模型只做结构分析、没有盈利断言。",
        "6. 查看 `candidate_registry.json` / `rejection_registry.json`，确认没有任何 `paper_champion`。",
    ]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
