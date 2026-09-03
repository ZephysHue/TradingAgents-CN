"""Reacquire public Bybit evidence with raw-page retention; no labels or market rules."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

BASE = "https://api.bybit.com"
WINDOW_START = pd.Timestamp("2026-06-01T00:00:00Z")
WINDOW_END = pd.Timestamp("2026-08-30T00:00:00Z")
MONTHS = ("2026-06", "2026-07", "2026-08")
SYMBOLS = (
    "0GUSDT", "1000000BABYDOGEUSDT", "1000000MOGUSDT", "10000NEXUSDT", "10000SATSUSDT",
    "1000BONKUSDT", "1000BTTUSDT", "1000CATUSDT", "1000FLOKIUSDT", "1000LUNCUSDT",
    "1000NEIROCTOUSDT", "1000PEPEUSDT", "1000RATSUSDT", "1000TAGUSDT", "1000TOSHIUSDT",
    "1000TURBOUSDT", "1000XECUSDT", "1INCHUSDT", "2ZUSDT", "4USDT",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def save_raw(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


@dataclass
class PublicFetcher:
    attempts: list[dict[str, Any]] = field(default_factory=list)
    connect_timeout: int = 10
    read_timeout: int = 30
    retries: int = 3

    def get(self, source_id: str, url: str, params: dict[str, Any] | None, raw_path: Path | None) -> dict[str, Any]:
        for number in range(1, self.retries + 1):
            started = utc_now()
            try:
                response = requests.get(url, params=params, timeout=(self.connect_timeout, self.read_timeout))
                response.raise_for_status()
                sha = hashlib.sha256(response.content).hexdigest()
                stored = None
                if raw_path is not None:
                    stored = str(raw_path)
                    save_raw(raw_path, response.content)
                event = {"source_id": source_id, "attempt": number, "utc": started, "url": response.url, "params": params or {}, "http_status": response.status_code, "exception": None, "response_sha256": sha, "raw_path": stored}
                self.attempts.append(event)
                try:
                    payload = response.json()
                except ValueError as exc:
                    return {"ok": False, "reason": f"invalid_json:{type(exc).__name__}", "event": event, "payload": None}
                return {"ok": True, "reason": None, "event": event, "payload": payload}
            except requests.RequestException as exc:
                event = {"source_id": source_id, "attempt": number, "utc": started, "url": url, "params": params or {}, "http_status": getattr(getattr(exc, "response", None), "status_code", None), "exception": type(exc).__name__, "response_sha256": None, "raw_path": None}
                self.attempts.append(event)
                if number < self.retries:
                    time.sleep(2 ** (number - 1))
                else:
                    return {"ok": False, "reason": f"network_request_failed:{type(exc).__name__}", "event": event, "payload": None}
        raise AssertionError("unreachable")


def historical_decision(launch_time_ms: str | None, rebalance: pd.Timestamp, has_time_bound_status: bool) -> tuple[str, str]:
    if launch_time_ms and int(launch_time_ms) >= int(rebalance.timestamp() * 1000):
        return "ineligible", "time_bound_launch_after_rebalance"
    if has_time_bound_status:
        return "verified", "time_bound_source_proves_eligibility"
    return "unverifiable", "no_time_bound_status_evidence"


def next_end_time(timestamps: list[int], prior_end: int) -> tuple[int | None, str]:
    if not timestamps:
        return None, "empty_page"
    candidate = min(timestamps) - 1
    if candidate >= prior_end:
        return None, "timestamp_not_advancing"
    return candidate, "continue"


def interval_summary(timestamps: list[int]) -> list[dict[str, Any]]:
    stamps = sorted(set(timestamps))
    if len(stamps) < 2:
        return []
    groups: dict[int, list[int]] = {}
    for left, right in zip(stamps, stamps[1:]):
        groups.setdefault(int(round((right - left) / 60_000)), []).append(right)
    return [{"interval_minutes": interval, "count": len(points), "first_end_utc": pd.to_datetime(min(points), unit="ms", utc=True).isoformat(), "last_end_utc": pd.to_datetime(max(points), unit="ms", utc=True).isoformat(), "share": len(points) / (len(stamps) - 1)} for interval, points in sorted(groups.items())]


def classify_timeline(metadata_minutes: int | None, intervals: list[dict[str, Any]], raw_complete: bool, network_failed: bool, has_unexplained_gap: bool) -> tuple[str, str]:
    if network_failed:
        return "network_evidence_unavailable", "raw_pages_not_acquired_due_to_network"
    if not raw_complete:
        return "not_reproducible_prior_trace", "raw_pages_not_complete_for_recalculation"
    observed = {row["interval_minutes"] for row in intervals}
    if has_unexplained_gap:
        return "pagination_gap", "saved_raw_pages_show_unexplained_timestamp_gap"
    if metadata_minutes is not None and observed == {metadata_minutes}:
        return "consistent", "all_observed_intervals_match_current_metadata"
    return "unresolved", "observed_interval_differs_from_current_metadata_without_time_bound_schedule_evidence"


def fetch_funding_pages(fetcher: PublicFetcher, symbol: str, raw_root: Path) -> dict[str, Any]:
    cursor = int(WINDOW_END.timestamp() * 1000)
    pages, all_records, stop_reason = [], [], "reached_research_window_start"
    network_failed = False
    for page_number in range(1, 513):
        path = raw_root / symbol / f"funding-{symbol}-page-{page_number:04d}-end-{cursor}.json"
        result = fetcher.get("bybit_v5_funding_history", f"{BASE}/v5/market/funding/history", {"category": "linear", "symbol": symbol, "limit": 200, "endTime": cursor}, path)
        manifest = {"symbol": symbol, "page": page_number, "end_time_ms": cursor, **result["event"], "result": "saved" if result["ok"] else result["reason"]}
        pages.append(manifest)
        if not result["ok"]:
            network_failed = True; stop_reason = result["reason"]; break
        payload = result["payload"]
        if payload.get("retCode") != 0:
            stop_reason = f"api_retcode_{payload.get('retCode')}"; break
        rows = payload.get("result", {}).get("list", [])
        manifest["record_count"] = len(rows)
        timestamps = []
        for row in rows:
            try:
                timestamps.append(int(row["fundingRateTimestamp"])); all_records.append(row)
            except (KeyError, TypeError, ValueError):
                manifest.setdefault("invalid_timestamp_records", 0); manifest["invalid_timestamp_records"] += 1
        manifest["earliest_timestamp_ms"] = min(timestamps) if timestamps else None
        manifest["latest_timestamp_ms"] = max(timestamps) if timestamps else None
        next_cursor, progress = next_end_time(timestamps, cursor)
        manifest["progress"] = progress
        if next_cursor is None:
            stop_reason = progress; break
        if min(timestamps) <= int(WINDOW_START.timestamp() * 1000):
            stop_reason = "reached_research_window_start"; break
        cursor = next_cursor
    else:
        stop_reason = "page_limit_reached"
    deduplicated = {int(row["fundingRateTimestamp"]): row for row in all_records if "fundingRateTimestamp" in row}
    return {"symbol": symbol, "pages": pages, "records": [deduplicated[key] for key in sorted(deduplicated)], "stop_reason": stop_reason, "network_failed": network_failed}


def main() -> int:
    parser = argparse.ArgumentParser(description="Bybit historical evidence reacquisition with raw public pages")
    parser.add_argument("--input", type=Path, default=Path("reports/crypto-backtest/multitier-mechanism-discovery-v1/universe-expansion-audit-v1"))
    parser.add_argument("--output", type=Path, default=Path("reports/crypto-backtest/multitier-mechanism-discovery-v1/historical-evidence-reacquisition-v1"))
    parser.add_argument("--connect-timeout", type=int, default=10)
    parser.add_argument("--read-timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    fetcher = PublicFetcher(connect_timeout=args.connect_timeout, read_timeout=args.read_timeout, retries=args.retries)
    raw_sources, raw_funding = args.output / "historical_source_raw", args.output / "funding_raw_pages"
    raw_sources.mkdir(parents=True, exist_ok=True); raw_funding.mkdir(parents=True, exist_ok=True)
    probes = [
        ("bybit_v5_instruments", f"{BASE}/v5/market/instruments-info", {"category": "linear", "limit": 1}),
        ("bybit_v5_funding", f"{BASE}/v5/market/funding/history", {"category": "linear", "symbol": "BTCUSDT", "limit": 1}),
        ("bybit_v5_announcements", f"{BASE}/v5/announcements/index", {"locale": "en-US", "limit": 1}),
        ("bybit_public_download", "https://public.bybit.com/", None),
    ]
    probe_rows, probe_results = [], {}
    for source_id, url, params in probes:
        result = fetcher.get(source_id, url, params, raw_sources / f"{source_id}.json")
        probe_results[source_id] = result
        probe_rows.append({"source_id": source_id, "ok": result["ok"], "reason": result["reason"], "event": result["event"]})
    probe_document = {"generated_at_utc": utc_now(), "tls_verification": "requests default verification", "connect_timeout_seconds": args.connect_timeout, "read_timeout_seconds": args.read_timeout, "retries": args.retries, "probes": probe_rows, "attempts": fetcher.attempts}
    write_json(args.output / "network_probe.json", probe_document); (args.output / "network_probe.sha256").write_text(canonical_sha256(probe_document) + "\n", encoding="utf-8")

    official_success = any(row["ok"] for row in probe_rows)
    prior_snapshot = json.loads((args.input / "current_instruments_snapshot.json").read_text(encoding="utf-8"))
    source_document = {"generated_at_utc": utc_now(), "sources": [{"source_id": row["source_id"], "official": True, "successful_reacquisition": row["ok"], "raw_path": row["event"]["raw_path"], "response_sha256": row["event"]["response_sha256"], "can_verify_symbol_month": False, "reason": "network_evidence_unavailable" if not official_success else "current or generic public endpoint does not itself provide a time-bound full symbol status interval"} for row in probe_rows]}
    write_json(args.output / "historical_source_reacquisition.json", source_document); (args.output / "historical_source_reacquisition.sha256").write_text(canonical_sha256(source_document) + "\n", encoding="utf-8")

    decision_columns = ["symbol", "month", "rebalance_timestamp_utc", "decision", "reason", "source_time_bound"]
    decisions = []
    if official_success:
        for entry in prior_snapshot["selected"]:
            for month in MONTHS:
                rebalance = pd.Timestamp(f"{month}-01T00:00:00Z")
                decision, reason = historical_decision(entry["raw"].get("launchTime"), rebalance, False)
                decisions.append({"symbol": entry["symbol"], "month": month, "rebalance_timestamp_utc": rebalance.isoformat(), "decision": decision, "reason": reason, "source_time_bound": False})
    decisions.sort(key=lambda x: (x["month"], x["symbol"]))
    pd.DataFrame(decisions, columns=decision_columns).to_csv(args.output / "historical_eligibility_reconciliation.csv", index=False)
    eligibility = {"generated_at_utc": utc_now(), "run_status": "network_evidence_unavailable" if not official_success else "completed", "rule": "current status and kline presence never produce verified", "decisions": decisions}
    write_json(args.output / "historical_eligibility_reconciliation.json", eligibility)

    if not official_success:
        status = "network_evidence_unavailable"; funding_runs = [{"symbol": symbol, "pages": [], "records": [], "stop_reason": "network_evidence_unavailable", "network_failed": True} for symbol in SYMBOLS]
    else:
        status = "historical_evidence_not_ready"; funding_runs = [fetch_funding_pages(fetcher, symbol, raw_funding) for symbol in SYMBOLS]
    manifest = {"generated_at_utc": utc_now(), "source": "Bybit V5 /v5/market/funding/history", "window_utc": [WINDOW_START.isoformat(), WINDOW_END.isoformat()], "contracts": [{key: item[key] for key in ("symbol", "pages", "stop_reason", "network_failed")} for item in funding_runs]}
    write_json(args.output / "funding_page_manifest.json", manifest); (args.output / "funding_page_manifest.sha256").write_text(canonical_sha256(manifest) + "\n", encoding="utf-8")
    metadata: dict[str, int | None] = {symbol: None for symbol in SYMBOLS}
    if probe_results["bybit_v5_instruments"]["ok"]:
        bulk = fetcher.get("bybit_v5_instruments_bulk", f"{BASE}/v5/market/instruments-info", {"category": "linear", "limit": 1000}, raw_sources / "bybit_v5_instruments_bulk.json")
        if bulk["ok"]:
            metadata = {item.get("symbol"): item.get("fundingInterval") for item in bulk["payload"].get("result", {}).get("list", []) if item.get("symbol") in SYMBOLS}
    rows = []
    for run in funding_runs:
        stamps = [int(record["fundingRateTimestamp"]) for record in run["records"]]
        intervals = interval_summary(stamps)
        start_utc = pd.to_datetime(min(stamps), unit="ms", utc=True).isoformat() if stamps else None
        end_utc = pd.to_datetime(max(stamps), unit="ms", utc=True).isoformat() if stamps else None
        complete = bool(stamps and min(stamps) <= int(WINDOW_START.timestamp() * 1000) and max(stamps) >= int(WINDOW_END.timestamp() * 1000) - 1 and not run["network_failed"])
        meta = metadata.get(run["symbol"])
        unexplained = bool(meta and any(item["interval_minutes"] > meta * 1.5 for item in intervals))
        classification, reason = classify_timeline(meta, intervals, complete, run["network_failed"], unexplained)
        rows.append({"symbol": run["symbol"], "raw_pages": len(run["pages"]), "earliest_utc": start_utc, "latest_utc": end_utc, "metadata_interval_minutes": meta, "observed_intervals": json.dumps(intervals), "research_window_complete": complete, "stop_reason": run["stop_reason"], "classification": classification, "classification_reason": reason, "evidence_status": "verified" if complete and classification in {"consistent", "unresolved", "pagination_gap"} else "pending_evidence"})
    reconciliation = {"generated_at_utc": utc_now(), "window_utc": [WINDOW_START.isoformat(), WINDOW_END.isoformat()], "contracts": rows}
    write_json(args.output / "funding_timeline_reconciliation.json", reconciliation); pd.DataFrame(rows).to_csv(args.output / "funding_timeline_reconciliation.csv", index=False); (args.output / "funding_timeline_reconciliation.sha256").write_text(canonical_sha256(reconciliation) + "\n", encoding="utf-8")
    report = ["# HISTORICAL_EVIDENCE_REACQUISITION_V1", "", f"- [COMPUTED] status={status}", f"- [COMPUTED] network probes succeeded={sum(x['ok'] for x in probe_rows)}/{len(probe_rows)}", "- [KNOWN] Current status is not historical status evidence.", "- [KNOWN] This audit does not create labels, costs, or market rules."]
    (args.output / "HISTORICAL_EVIDENCE_REACQUISITION_V1.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "network_probe_successes": sum(x["ok"] for x in probe_rows), "funding_contracts": len(rows), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
