"""Bybit USDT-linear universe expansion and historical-availability audit only."""
from __future__ import annotations

import argparse, hashlib, json, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

try:
    from .census_multitier_universe_v1 import INTERVAL_MS, classify_tiers, digest, maximum_missing_run
except ImportError:
    from census_multitier_universe_v1 import INTERVAL_MS, classify_tiers, digest, maximum_missing_run

BASE = "https://api.bybit.com"

def get_json(path: str, params: dict[str, Any]) -> tuple[dict[str, Any], str]:
    response = requests.get(f"{BASE}{path}", params=params, timeout=30)
    response.raise_for_status(); body = response.json()
    if body.get("retCode") != 0: raise RuntimeError(body.get("retMsg", "Bybit API error"))
    return body["result"], hashlib.sha256(response.content).hexdigest()

def current_instruments() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cursor, pages, all_items = None, [], []
    while True:
        params = {"category": "linear", "limit": 1000}
        if cursor: params["cursor"] = cursor
        result, response_hash = get_json("/v5/market/instruments-info", params)
        page = result.get("list", []); all_items.extend(page); pages.append({"params": params, "response_sha256": response_hash, "rows": len(page)})
        cursor = result.get("nextPageCursor")
        if not cursor: break
    selected, excluded = [], []
    for item in all_items:
        reason = None
        if item.get("settleCoin") != "USDT": reason = "settle_coin_not_usdt"
        elif item.get("contractType") != "LinearPerpetual": reason = "contract_type_not_linear_perpetual"
        elif item.get("status") != "Trading": reason = "current_status_not_trading"
        (excluded if reason else selected).append({"symbol": item.get("symbol"), "reason": reason, "raw": item})
    return selected, excluded, pages

def historical_status(item: dict[str, Any], month: pd.Timestamp) -> tuple[str, str]:
    launch = item.get("launchTime")
    if launch and int(launch) >= int(month.timestamp() * 1000): return "ineligible", "launch_time_not_before_reconstitution"
    return "unverifiable", "current_instruments_info_has_no_historical_status_snapshot"

def fetch_month(symbol: str, start: pd.Timestamp, cache: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    end = start + pd.Timedelta(days=30); path = cache / f"{symbol}-{start:%Y%m%d}-15m.csv.gz"
    if path.exists():
        frame = pd.read_csv(path); frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        return frame.set_index("timestamp"), {"cache_hit": True, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    rows, cursor, end_ms = [], int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    while cursor < end_ms:
        result, _ = get_json("/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": "15", "start": cursor, "end": min(cursor + 1000 * INTERVAL_MS - 1, end_ms - 1), "limit": 1000})
        page = result.get("list", [])
        if not page: cursor += 1000 * INTERVAL_MS; continue
        rows.extend(page); cursor = max(int(row[0]) for row in page) + INTERVAL_MS
    frame = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "turnover"])
    if frame.empty: raise RuntimeError("empty_kline_response")
    frame["open_time"] = pd.to_numeric(frame.open_time); frame = frame.drop_duplicates("open_time").sort_values("open_time")
    for column in ("open", "high", "low", "close", "volume", "turnover"): frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["timestamp"] = pd.to_datetime(frame.open_time, unit="ms", utc=True); frame = frame.dropna()
    frame.to_csv(path, index=False, compression="gzip")
    return frame.set_index("timestamp"), {"cache_hit": False, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

def quality(symbol: str, month: pd.Timestamp, state: str, item: dict[str, Any], cache: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    start = month - pd.Timedelta(days=30); expected = pd.date_range(start, month - pd.Timedelta(minutes=15), freq="15min", tz="UTC")
    base = {"month": month.strftime("%Y-%m"), "reconstitution_time_utc": month.isoformat(), "lookback_start_utc": start.isoformat(), "lookback_end_exclusive_utc": month.isoformat(), "symbol": symbol, "historical_eligibility": state}
    if state == "ineligible": return {**base, "expected_15m_bars": len(expected), "observed_15m_bars": 0, "coverage_ratio": 0.0, "max_consecutive_missing_bars": len(expected), "turnover_30d": 0.0, "quality_reason": "not_listed_before_reconstitution", "strict_universe": False, "provisional_universe": False}, {"symbol": symbol, "month": month.strftime("%Y-%m"), "status": "not_requested_ineligible"}
    try:
        frame, manifest = fetch_month(symbol, start, cache); observed = frame.index.unique(); coverage = len(observed.intersection(expected)) / len(expected); gap = maximum_missing_run(observed, expected); turnover = float(frame.loc[frame.index.isin(expected), "turnover"].sum())
        pass_quality = coverage >= .99 and gap <= 4 and turnover > 0
        reason = "quality_pass" if pass_quality else ("coverage_below_99pct" if coverage < .99 else "missing_run_exceeds_4_bars" if gap > 4 else "nonpositive_turnover")
        return {**base, "expected_15m_bars": len(expected), "observed_15m_bars": int(len(observed.intersection(expected))), "coverage_ratio": coverage, "max_consecutive_missing_bars": gap, "turnover_30d": turnover, "turnover_p10": float(frame.turnover.quantile(.1)), "turnover_median": float(frame.turnover.median()), "turnover_p90": float(frame.turnover.quantile(.9)), "quality_reason": reason, "strict_universe": False, "provisional_universe": bool(pass_quality and state == "unverifiable")}, {**manifest, "symbol": symbol, "month": month.strftime("%Y-%m"), "source": "Bybit V5 /v5/market/kline", "rows": len(frame), "utc_start": frame.index.min().isoformat(), "utc_end": frame.index.max().isoformat()}
    except Exception as exc:
        return {**base, "expected_15m_bars": len(expected), "observed_15m_bars": 0, "coverage_ratio": 0.0, "max_consecutive_missing_bars": len(expected), "turnover_30d": 0.0, "quality_reason": f"kline_unavailable_{type(exc).__name__}", "strict_universe": False, "provisional_universe": False}, {"symbol": symbol, "month": month.strftime("%Y-%m"), "status": f"request_failed_{type(exc).__name__}"}

def funding_probe(symbol: str, item: dict[str, Any], start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
    requests_audit = []
    try:
        # The documented API has no cursor; use bounded endTime pagination backward.
        cursor, stamps, pages = int(end.timestamp() * 1000), [], 0
        while cursor > int(start.timestamp() * 1000) and pages < 4:
            params = {"category": "linear", "symbol": symbol, "endTime": cursor, "limit": 200}; result, response_hash = get_json("/v5/market/funding/history", params)
            rows = result.get("list", []); requests_audit.append({"params": params, "response_sha256": response_hash, "rows": len(rows)})
            page = sorted({int(row["fundingRateTimestamp"]) for row in rows}, reverse=True)
            if not page: break
            stamps.extend(page); next_cursor = min(page) - 1
            if next_cursor >= cursor: break
            cursor, pages = next_cursor, pages + 1
        stamps = sorted(set(stamps)); diff = np.diff(stamps); intervals = sorted({round(value / 3_600_000, 6) for value in diff})
        full = bool(stamps) and stamps[0] <= int(start.timestamp() * 1000) and stamps[-1] >= int(end.timestamp() * 1000) - 1
        expected_hours = float(item.get("fundingInterval", 0)) / 60 if item.get("fundingInterval") else None
        gaps = int(sum(value > expected_hours * 3_600_000 * 1.5 for value in diff)) if expected_hours else None
        return {"symbol": symbol, "status": "available" if full else "pending_evidence", "documentation": "Bybit V5 funding history supports endTime and limit <=200; no cursor field documented.", "metadata_interval_minutes": item.get("fundingInterval"), "observed_intervals_hours": intervals, "coverage_start_utc": pd.to_datetime(stamps[0], unit="ms", utc=True).isoformat() if stamps else None, "coverage_end_utc": pd.to_datetime(stamps[-1], unit="ms", utc=True).isoformat() if stamps else None, "requested_start_utc": start.isoformat(), "requested_end_utc": end.isoformat(), "records": len(stamps), "gap_count": gaps, "requests": requests_audit}
    except Exception as exc: return {"symbol": symbol, "status": "pending_evidence", "reason": type(exc).__name__, "requests": requests_audit}

def write_json(path: Path, value: Any) -> None: path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

def tier_summary(rows: list[dict[str, Any]], universe: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result, registry = [], {"universe": universe, "months": []}
    for month in sorted({row["month"] for row in rows}):
        eligible = [row for row in rows if row["month"] == month and row[universe]]; ordered = sorted(eligible, key=lambda x: (-x["turnover_30d"], x["symbol"])); memberships = classify_tiers(ordered); tiers = {}
        for tier, symbols in memberships.items(): tiers[tier] = {"symbols": symbols, "count": len(symbols), "status": "available" if len(symbols) >= 5 else "insufficient_universe"}; result.append({"universe": universe, "month": month, "tier": tier, **tiers[tier]})
        registry["months"].append({"month": month, "eligible_contracts": len(eligible), "tiers": tiers})
    return result, registry

def main() -> int:
    p = argparse.ArgumentParser(description="Unlabelled dynamic Bybit universe audit")
    p.add_argument("--start", default="2026-06-01"); p.add_argument("--end", default="2026-08-29")
    p.add_argument("--workers", type=int, default=16); p.add_argument("--cache", type=Path, default=Path("research/crypto_backtest/data/multitier-expansion-v1")); p.add_argument("--output", type=Path, default=Path("reports/crypto-backtest/multitier-mechanism-discovery-v1/universe-expansion-audit-v1")); a = p.parse_args(); a.cache.mkdir(parents=True, exist_ok=True); a.output.mkdir(parents=True, exist_ok=True)
    start, end = pd.Timestamp(a.start, tz="UTC"), pd.Timestamp(a.end, tz="UTC") + pd.Timedelta(days=1); months = list(pd.date_range(start, end, freq="MS", tz="UTC")); months = [m for m in months if m < end]
    selected, excluded, pages = current_instruments(); fetched = datetime.now(timezone.utc).isoformat(); snapshot = {"fetched_at_utc": fetched, "endpoint": "/v5/market/instruments-info", "pages": pages, "selected": selected, "excluded": excluded}; write_json(a.output / "current_instruments_snapshot.json", snapshot); (a.output / "current_instruments_snapshot.sha256").write_text(digest(snapshot)+"\n")
    evidence = {"endpoint": "/v5/market/instruments-info", "request_boundary": "current state and launchTime only; no historical status snapshots returned", "fetched_at_utc": fetched, "contracts": []}; quality_rows, manifests = [], []
    jobs = []
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for entry in selected:
            symbol, raw = entry["symbol"], entry["raw"]
            for month in months:
                state, reason = historical_status(raw, month); evidence["contracts"].append({"symbol": symbol, "month": month.strftime("%Y-%m"), "historical_eligibility": state, "reason": reason, "launch_time": raw.get("launchTime")}); jobs.append(pool.submit(quality, symbol, month, state, raw, a.cache))
        for job in as_completed(jobs):
            row, manifest = job.result(); quality_rows.append(row); manifests.append(manifest)
    evidence["contracts"].sort(key=lambda x:(x["month"],x["symbol"])); pd.DataFrame(evidence["contracts"]).to_csv(a.output / "historical_eligibility_audit.csv", index=False); write_json(a.output / "historical_eligibility_evidence.json", evidence)
    pd.DataFrame(quality_rows).sort_values(["month","symbol"]).to_csv(a.output / "expanded_data_quality.csv", index=False); write_json(a.output / "expanded_data_manifest.json", {"generated_at_utc": fetched, "records": manifests})
    liquidity = pd.DataFrame(quality_rows)[["month","symbol","coverage_ratio","max_consecutive_missing_bars","turnover_30d","turnover_p10","turnover_median","turnover_p90","strict_universe","provisional_universe","quality_reason"]]; liquidity.to_csv(a.output / "expanded_liquidity_census.csv", index=False)
    strict_rows, strict = tier_summary(quality_rows, "strict_universe"); provisional_rows, provisional = tier_summary(quality_rows, "provisional_universe"); write_json(a.output / "strict_historical_universe_registry.json", strict); (a.output / "strict_historical_universe_registry.sha256").write_text(digest(strict)+"\n"); write_json(a.output / "provisional_current_listing_universe_registry.json", provisional); (a.output / "provisional_current_listing_universe_registry.sha256").write_text(digest(provisional)+"\n"); pd.DataFrame(strict_rows).to_csv(a.output / "strict_tier_summary.csv", index=False); pd.DataFrame(provisional_rows).to_csv(a.output / "provisional_tier_summary.csv", index=False)
    probes = [funding_probe(entry["symbol"], entry["raw"], start - pd.Timedelta(days=30), end) for entry in selected[:20]]; write_json(a.output / "funding_api_capability_audit.json", {"generated_at_utc": fetched, "scope": "first 20 symbols in deterministic current-instrument order", "contracts": probes})
    costs = {"generated_at_utc": fetched, "tiers": {tier: {"round_trip_fee": None, "one_way_slippage": None, "funding_cost": None, "edge_floor": None, "evidence_status": "pending_evidence", "reason": "No tier-level reproducible fee, slippage, and complete funding evidence."} for tier in ("hot","mid","low")}}; write_json(a.output / "cost_evidence_audit.json", costs)
    report = ["# UNIVERSE_EXPANSION_AUDIT_V1", "", f"- [COMPUTED] Current dynamic candidates: {len(selected)}; current exclusions: {len(excluded)}.", "- [KNOWN] Current status is recorded as a snapshot and is never treated as historical status.", "- [COMPUTED] Strict historical universe is empty because the queried public endpoint has no historical status snapshots.", "- [KNOWN] Cost evidence remains pending; this audit stops before any labelled inspection."]
    (a.output / "UNIVERSE_EXPANSION_AUDIT_V1.md").write_text("\n".join(report)+"\n", encoding="utf-8"); print(json.dumps({"status":"PASS","candidates":len(selected),"months":len(months),"output":str(a.output)}))
if __name__ == "__main__": main()
