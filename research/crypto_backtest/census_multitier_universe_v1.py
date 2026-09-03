"""Unlabelled Bybit linear-perpetual universe census for multitier mechanism v1."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

API = "https://api.bybit.com"
INTERVAL_MS = 15 * 60 * 1000
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def utc(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def maximum_missing_run(observed: pd.DatetimeIndex, expected: pd.DatetimeIndex) -> int:
    present = expected.isin(observed)
    longest = current = 0
    for available in present:
        current = 0 if available else current + 1
        longest = max(longest, current)
    return longest


def tier_positions(count: int) -> dict[str, list[int]]:
    """Return one-based ranks; percentile endpoints are rounded inward."""
    return {
        "hot": list(range(1, min(10, count) + 1)),
        "mid": list(range(int(np.ceil(count * .45)), int(np.floor(count * .55)) + 1)),
        "low": list(range(int(np.ceil(count * .75)), int(np.floor(count * .85)) + 1)),
    }


def classify_tiers(eligible: list[dict[str, Any]]) -> dict[str, list[str]]:
    ordered = sorted(eligible, key=lambda item: (-item["turnover_30d"], item["symbol"]))
    positions = tier_positions(len(ordered))
    by_tier: dict[str, list[str]] = {}
    for tier, ranks in positions.items():
        by_tier[tier] = [ordered[rank - 1]["symbol"] for rank in ranks if 1 <= rank <= len(ordered)]
    return by_tier


def api_get(session: requests.Session, path: str, params: dict[str, Any]) -> dict[str, Any]:
    response = session.get(f"{API}{path}", params=params, timeout=30)
    response.raise_for_status()
    body = response.json()
    if body.get("retCode") != 0:
        raise RuntimeError(body.get("retMsg", "Bybit API error"))
    return body["result"]


def instrument_snapshot(session: requests.Session, symbols: tuple[str, ...]) -> tuple[dict[str, dict[str, Any]], str]:
    payload = api_get(session, "/v5/market/instruments-info", {"category": "linear", "limit": 1000})
    fetched_at = datetime.now(timezone.utc).isoformat()
    items = {item["symbol"]: item for item in payload.get("list", [])}
    return {symbol: items.get(symbol, {}) for symbol in symbols}, fetched_at


def fetch_klines(session: requests.Session, symbol: str, start: pd.Timestamp, end: pd.Timestamp, cache: Path) -> tuple[pd.DataFrame, bool]:
    path = cache / f"{symbol}-15m-{start.date()}-{(end - pd.Timedelta(days=1)).date()}.csv"
    if path.exists():
        frame = pd.read_csv(path)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        return frame.set_index("timestamp").sort_index(), True
    rows: list[list[str]] = []
    cursor, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    while cursor < end_ms:
        result = api_get(session, "/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": "15", "start": cursor, "end": min(cursor + 1000 * INTERVAL_MS - 1, end_ms - 1), "limit": 1000})
        page = result.get("list", [])
        if not page:
            cursor += 1000 * INTERVAL_MS
            continue
        rows.extend(page)
        cursor = max(int(row[0]) for row in page) + INTERVAL_MS
        time.sleep(.06)
    frame = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "turnover"])
    if frame.empty:
        raise RuntimeError(f"no 15m records returned for {symbol}")
    frame["open_time"] = pd.to_numeric(frame["open_time"])
    frame = frame.drop_duplicates("open_time").sort_values("open_time")
    for column in ("open", "high", "low", "close", "volume", "turnover"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["timestamp"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    frame = frame[(frame.timestamp >= start) & (frame.timestamp < end)].dropna().copy()
    frame.to_csv(path, index=False)
    return frame.set_index("timestamp").sort_index(), False


def quality_row(symbol: str, month: pd.Timestamp, frame: pd.DataFrame, metadata: dict[str, Any], metadata_time: str) -> dict[str, Any]:
    window_start, window_end = month - pd.Timedelta(days=30), month
    sample = frame.loc[(frame.index >= window_start) & (frame.index < window_end)]
    expected = pd.date_range(window_start, window_end - pd.Timedelta(minutes=15), freq="15min", tz="UTC")
    coverage = len(sample.index.unique().intersection(expected)) / len(expected)
    max_gap = maximum_missing_run(sample.index.unique(), expected)
    metadata_ok = metadata.get("settleCoin") == "USDT" and metadata.get("contractType") == "LinearPerpetual" and metadata.get("status") == "Trading"
    reason = "eligible"
    if not metadata_ok:
        reason = "instrument_not_currently_usdt_linear_trading"
    elif coverage < .99:
        reason = "coverage_below_99pct"
    elif max_gap > 4:
        reason = "missing_run_exceeds_4_bars"
    elif sample.turnover.sum() <= 0:
        reason = "nonpositive_turnover"
    return {"month": month.strftime("%Y-%m"), "reconstitution_time_utc": month.isoformat(), "lookback_start_utc": window_start.isoformat(), "lookback_end_exclusive_utc": window_end.isoformat(), "symbol": symbol, "settle_coin": metadata.get("settleCoin"), "contract_type": metadata.get("contractType"), "status_snapshot": metadata.get("status"), "instrument_metadata_fetched_at": metadata_time, "historical_instrument_status": "pending_evidence_current_snapshot_only", "expected_15m_bars": len(expected), "observed_15m_bars": len(sample), "coverage_ratio": coverage, "max_consecutive_missing_bars": max_gap, "turnover_30d": float(sample.turnover.sum()), "eligibility_reason": reason, "eligible": reason == "eligible"}


def regime_rows(quality: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    month = utc(quality["reconstitution_time_utc"])
    sample = frame.loc[(frame.index >= month - pd.Timedelta(days=30)) & (frame.index < month)].copy()
    previous = sample.close.shift(1)
    true_range = pd.concat([sample.high - sample.low, (sample.high - previous).abs(), (sample.low - previous).abs()], axis=1).max(axis=1)
    atr_pct = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / sample.close
    realized = sample.close.pct_change().rolling(96, min_periods=96).std() * np.sqrt(365 * 24 * 4)
    range_pct = (sample.high - sample.low) / sample.close
    def spread(series: pd.Series, prefix: str) -> dict[str, float]:
        return {f"{prefix}_{name}": float(series.quantile(q)) for name, q in (("p10", .10), ("median", .50), ("p90", .90))}
    return {"month": quality["month"], "symbol": quality["symbol"], "coverage_ratio": quality["coverage_ratio"], "max_consecutive_missing_bars": quality["max_consecutive_missing_bars"], **spread(sample.turnover, "turnover_15m"), **spread(atr_pct.dropna(), "atr_pct"), **spread(realized.dropna(), "realized_vol_annualized"), **spread(range_pct.dropna(), "range_pct")}


def funding_availability(session: requests.Session, symbol: str, start: pd.Timestamp, end: pd.Timestamp, metadata: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    try:
        result = api_get(session, "/v5/market/funding/history", {"category": "linear", "symbol": symbol, "startTime": int(start.timestamp() * 1000), "endTime": int(end.timestamp() * 1000), "limit": 200})
        records = result.get("list", [])
        stamps = sorted(int(row["fundingRateTimestamp"]) for row in records)
        diffs = np.diff(stamps) if len(stamps) > 1 else np.array([])
        actual_hours = sorted({round(float(value) / 3_600_000, 6) for value in diffs})
        expected_minutes = metadata.get("fundingInterval")
        expected_hours = float(expected_minutes) / 60 if expected_minutes else None
        gaps = int(sum(value > (expected_hours * 3_600_000 * 1.5) for value in diffs)) if expected_hours else None
        complete = bool(stamps) and stamps[0] <= int(start.timestamp() * 1000) and stamps[-1] >= int(end.timestamp() * 1000) - 1
        return {"symbol": symbol, "status": "available" if complete else "pending_evidence", "source": "Bybit V5 /v5/market/funding/history", "fetched_at_utc": fetched_at, "requested_start_utc": start.isoformat(), "requested_end_exclusive_utc": end.isoformat(), "available_start_utc": pd.to_datetime(stamps[0], unit="ms", utc=True).isoformat() if stamps else None, "available_end_utc": pd.to_datetime(stamps[-1], unit="ms", utc=True).isoformat() if stamps else None, "records": len(records), "metadata_funding_interval_minutes": expected_minutes, "metadata_funding_interval_hours": expected_hours, "observed_intervals_hours": actual_hours, "gap_count": gaps, "note": "Observed timestamps are recorded; current request is marked pending when the returned range does not cover the requested range."}
    except Exception as exc:
        return {"symbol": symbol, "status": "pending_evidence", "source": "Bybit V5 /v5/market/funding/history", "fetched_at_utc": fetched_at, "requested_start_utc": start.isoformat(), "requested_end_exclusive_utc": end.isoformat(), "available_start_utc": None, "available_end_utc": None, "records": 0, "metadata_funding_interval_hours": metadata.get("fundingInterval"), "observed_intervals_hours": [], "gap_count": None, "note": f"reproducible fetch unavailable: {type(exc).__name__}"}


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Unlabelled Bybit multitier census only")
    parser.add_argument("--start", default="2026-06-01", help="first census month, UTC")
    parser.add_argument("--end", default="2026-08-29", help="last complete data day, UTC")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--cache", type=Path, default=Path("research/crypto_backtest/data/multitier-mechanism-v1"))
    parser.add_argument("--output", type=Path, default=Path("reports/crypto-backtest/multitier-mechanism-discovery-v1"))
    args = parser.parse_args(); args.cache.mkdir(parents=True, exist_ok=True); args.output.mkdir(parents=True, exist_ok=True)
    start, end = utc(args.start), utc(args.end) + pd.Timedelta(days=1)
    months = list(pd.date_range(start.normalize(), end.normalize(), freq="MS", tz="UTC"))
    months = [month for month in months if month < end]
    data_start = min(months) - pd.Timedelta(days=30)
    session = requests.Session(); metadata, fetched_at = instrument_snapshot(session, tuple(args.symbols))
    data: dict[str, pd.DataFrame] = {}; manifest_records = []
    for symbol in args.symbols:
        frame, cache_hit = fetch_klines(session, symbol, data_start, end, args.cache)
        data[symbol] = frame; source = next(args.cache.glob(f"{symbol}-15m-*.csv")); payload = source.read_bytes()
        manifest_records.append({"symbol": symbol, "timeframe": "15m", "utc_start": frame.index.min().isoformat(), "utc_end": frame.index.max().isoformat(), "rows": len(frame), "sha256": hashlib.sha256(payload).hexdigest(), "cache_hit": cache_hit, "source": "Bybit V5 /v5/market/kline", "downloaded_at_utc": fetched_at, "path": str(source)})
    quality = [quality_row(symbol, month, data[symbol], metadata[symbol], fetched_at) for month in months for symbol in args.symbols]
    monthly_tiers = []
    for month in months:
        rows = [row for row in quality if row["month"] == month.strftime("%Y-%m")]; eligible = [row for row in rows if row["eligible"]]
        for rank, row in enumerate(sorted(eligible, key=lambda item: (-item["turnover_30d"], item["symbol"])), start=1): row["rank"] = rank
        memberships = classify_tiers(eligible)
        tier_state = {tier: {"symbols": symbols, "count": len(symbols), "status": "available" if len(symbols) >= 5 else "insufficient_universe"} for tier, symbols in memberships.items()}
        monthly_tiers.append({"month": month.strftime("%Y-%m"), "eligible_contracts": len(eligible), "tiers": tier_state})
        for row in rows:
            member_tiers = [tier for tier, symbols in memberships.items() if row["symbol"] in symbols]
            row["rank"] = row.get("rank"); row["tier"] = next((tier for tier in ("hot", "mid", "low") if tier in member_tiers), None)
            row["tier_memberships"] = ",".join(member_tiers); row["tier_insufficient_memberships"] = ",".join(tier for tier in member_tiers if tier_state[tier]["status"] == "insufficient_universe")
            row["tier_status"] = "available" if row["tier"] and tier_state[row["tier"]]["status"] == "available" else ("insufficient_universe" if member_tiers else "not_selected")
    regimes = [regime_rows(row, data[row["symbol"]]) for row in quality]
    registry = {"version": "multitier-mechanism-discovery-v1-census", "labelled_analysis": "not_run", "timezone": "UTC", "reconstitution_rule": "month start; uses [month_start-30d, month_start) completed 15m bars only", "tier_definition": {"hot": "rank 1-10", "mid": "ceil(45pct) through floor(55pct)", "low": "ceil(75pct) through floor(85pct)"}, "historical_instrument_status_boundary": "Bybit public instruments endpoint is a current snapshot; historical status is pending evidence.", "monthly_tiers": monthly_tiers, "records": quality}
    funding = {"version": "v1", "generated_at_utc": fetched_at, "contracts": [funding_availability(session, symbol, data_start, end, metadata[symbol], fetched_at) for symbol in args.symbols]}
    cost = {"version": "v1", "generated_at_utc": fetched_at, "method": "edge_floor = round_trip_fee + 2 * one_way_slippage", "tiers": {tier: {"status": "pending_evidence", "round_trip_fee": None, "one_way_slippage": None, "edge_floor": None, "reason": "No tier-specific reproducible historical fee and slippage evidence was supplied or fetched in this unlabelled census."} for tier in ("hot", "mid", "low")}}
    write_json(args.output / "universe_registry.json", registry); (args.output / "universe_registry.sha256").write_text(digest(registry) + "\n", encoding="utf-8")
    write_json(args.output / "data_manifest.json", {"source": "Bybit V5", "generated_at_utc": fetched_at, "records": manifest_records}); pd.DataFrame(quality).to_csv(args.output / "data_quality.csv", index=False); pd.DataFrame(regimes).to_csv(args.output / "regime_census.csv", index=False)
    write_json(args.output / "funding_data_availability.json", funding); write_json(args.output / "cost_floor_registry.json", cost); (args.output / "cost_floor_registry.sha256").write_text(digest(cost) + "\n", encoding="utf-8")
    tier_counts = {tier: sum(month["tiers"][tier]["status"] == "available" for month in monthly_tiers) for tier in ("hot", "mid", "low")}
    insufficient = {tier: sum(month["tiers"][tier]["status"] == "insufficient_universe" for month in monthly_tiers) for tier in ("hot", "mid", "low")}
    report = ["# MULTITIER_UNIVERSE_CENSUS_V1", "", "- [KNOWN] This is an unlabelled data census only.", "- [KNOWN] No label calculation or rule search was run.", f"- [COMPUTED] Census months: {len(months)}; contracts: {len(args.symbols)}.", f"- [COMPUTED] Available tier months: {tier_counts}; insufficient tier months: {insufficient}.", "- [KNOWN] Cost registry remains pending evidence for every tier.", "- [KNOWN] Historical contract-status evidence remains pending because the public instruments endpoint is a current snapshot.", "- [COMPUTED] Therefore this census does not meet the gate for Development mechanism inspection."]
    (args.output / "MULTITIER_UNIVERSE_CENSUS_V1.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "months": len(months), "contracts": len(args.symbols), "labelled_analysis": "not_run", "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
