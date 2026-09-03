"""Audit historical Bybit instrument and funding evidence without market-rule research."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

BASE = "https://api.bybit.com"
MONTHS = ("2026-06", "2026-07", "2026-08")
AUDIT_VERSION = "historical-evidence-audit-v1"


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def request_public(url: str, params: dict[str, Any] | None = None, timeout: int = 8) -> dict[str, Any]:
    """Record a TLS-verified public request without converting its content into historical status."""
    captured = datetime.now(timezone.utc).isoformat()
    try:
        response = requests.get(url, params=params, timeout=timeout)  # requests verifies TLS by default.
        payload: Any
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return {
            "request_url": response.url,
            "request_params": params or {},
            "fetched_at_utc": captured,
            "http_status": response.status_code,
            "response_sha256": hashlib.sha256(response.content).hexdigest(),
            "response_bytes": len(response.content),
            "json_top_level_keys": sorted(payload) if isinstance(payload, dict) else None,
            "error": None,
        }
    except requests.RequestException as exc:
        return {
            "request_url": url,
            "request_params": params or {},
            "fetched_at_utc": captured,
            "http_status": None,
            "response_sha256": None,
            "response_bytes": 0,
            "json_top_level_keys": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def decide_historical_eligibility(launch_time_ms: str | None, rebalance: pd.Timestamp, has_historical_state: bool) -> tuple[str, str]:
    if launch_time_ms and int(launch_time_ms) >= int(rebalance.timestamp() * 1000):
        return "ineligible", "launch_time_not_before_rebalance"
    if has_historical_state:
        return "verified", "reproducible_historical_state_evidence"
    return "unverifiable", "no_reproducible_historical_status_snapshot"


def validate_backward_page_trace(pages: list[dict[str, Any]]) -> tuple[bool, str]:
    prior_end: int | None = None
    for page in pages:
        end = page.get("params", {}).get("endTime")
        if not isinstance(end, int):
            return False, "missing_end_time"
        if prior_end is not None and end >= prior_end:
            return False, "end_time_not_strictly_decreasing"
        prior_end = end
    return True, "strictly_decreasing" if pages else "no_pages"


def normalise_funding_timestamps(values: list[int]) -> list[int]:
    """Deduplicate then order raw funding timestamps before interval inspection."""
    return sorted(set(values))


def classify_interval(metadata_minutes: int | None, observed_minutes: list[int], trace_complete: bool) -> tuple[str, str]:
    if not observed_minutes:
        return "unresolved", "no_observed_timestamps"
    if not trace_complete:
        return "pagination_gap", "raw_pages_or_full_trace_unavailable"
    if metadata_minutes is not None and all(value == metadata_minutes for value in observed_minutes):
        return "consistent", "metadata_matches_observed"
    return "unresolved", "metadata_history_or_schedule_change_cannot_be_distinguished"


def fetch_funding_timeline(symbol: str, start: pd.Timestamp, end: pd.Timestamp, timeout: int, max_pages: int = 512) -> dict[str, Any]:
    """Fetch public funding pages backwards; never invent timestamps if a page is unavailable."""
    end_time = int(end.timestamp() * 1000)
    start_time = int(start.timestamp() * 1000)
    pages: list[dict[str, Any]] = []
    timestamps: list[int] = []
    failure: str | None = None
    stop_reason = "research_window_reached"
    while end_time > start_time and len(pages) < max_pages:
        params = {"category": "linear", "symbol": symbol, "endTime": end_time, "limit": 200}
        captured = datetime.now(timezone.utc).isoformat()
        try:
            raw = requests.get(f"{BASE}/v5/market/funding/history", params=params, timeout=timeout)
            raw.raise_for_status()
            result = raw.json().get("result", {})
            page = {"request_url": raw.url, "request_params": params, "fetched_at_utc": captured, "http_status": raw.status_code, "response_sha256": hashlib.sha256(raw.content).hexdigest(), "response_bytes": len(raw.content), "error": None}
        except (requests.RequestException, ValueError) as exc:
            failure = f"{type(exc).__name__}: {exc}"
            page = {"request_url": f"{BASE}/v5/market/funding/history", "request_params": params, "fetched_at_utc": captured, "http_status": None, "response_sha256": None, "response_bytes": 0, "error": failure, "rows": 0}
            pages.append(page); stop_reason = "request_failure"; break
        try:
            rows = result.get("list", [])
        except AttributeError:
            failure = "invalid_result_shape"; page["rows"] = 0; pages.append(page); stop_reason = "body_parse_failure"; break
        page["rows"] = len(rows)
        page_timestamps = sorted({int(row["fundingRateTimestamp"]) for row in rows if "fundingRateTimestamp" in row})
        page["first_timestamp_utc"] = pd.to_datetime(page_timestamps[0], unit="ms", utc=True).isoformat() if page_timestamps else None
        page["last_timestamp_utc"] = pd.to_datetime(page_timestamps[-1], unit="ms", utc=True).isoformat() if page_timestamps else None
        pages.append(page)
        if not page_timestamps:
            stop_reason = "empty_page"; break
        earliest = page_timestamps[0]
        if earliest >= end_time:
            failure = "pagination_stalled"; stop_reason = "pagination_stalled"; break
        timestamps.extend(page_timestamps)
        end_time = earliest - 1
    else:
        if len(pages) >= max_pages:
            stop_reason = "max_pages_reached"
    return {"pages": pages, "timestamps": normalise_funding_timestamps(timestamps), "failure": failure, "stop_reason": stop_reason}


def build_source_inventory(previous_snapshot: dict[str, Any], timeout: int) -> list[dict[str, Any]]:
    live_instruments = request_public(f"{BASE}/v5/market/instruments-info", {"category": "linear", "limit": 1}, timeout)
    live_announcements = request_public(f"{BASE}/v5/announcements/index", {"locale": "en-US", "limit": 1}, timeout)
    live_public_download = request_public("https://public.bybit.com/", None, timeout)
    live_archive = request_public(
        "https://web.archive.org/cdx/search/cdx",
        {"url": "api.bybit.com/v5/market/instruments-info*", "output": "json", "filter": "statuscode:200", "limit": 1},
        timeout,
    )
    previous_page_hashes = [page.get("response_sha256") for page in previous_snapshot.get("pages", [])]
    return [
        {
            "source_id": "bybit_v5_instruments_current",
            "name": "Bybit V5 Get Instruments Info",
            "url": f"{BASE}/v5/market/instruments-info",
            "official": True,
            "public_reproducible": live_instruments["error"] is None,
            "historical_timepoint_support": False,
            "fields": ["symbol", "status", "launchTime", "deliveryTime", "fundingInterval"],
            "covers_months": False,
            "can_verify_symbol_month": False,
            "reason": "current endpoint and prior captured response only; no historical status snapshot parameter or response version",
            "prior_response_sha256": previous_page_hashes,
            "live_request": live_instruments,
        },
        {
            "source_id": "bybit_v5_announcements_index",
            "name": "Bybit V5 Announcements Index",
            "url": f"{BASE}/v5/announcements/index",
            "official": True,
            "public_reproducible": live_announcements["error"] is None,
            "historical_timepoint_support": False,
            "fields": ["announcement metadata only when available"],
            "covers_months": False,
            "can_verify_symbol_month": False,
            "reason": "no audited machine association from every symbol to a full status interval",
            "live_request": live_announcements,
        },
        {
            "source_id": "bybit_public_download_root",
            "name": "Bybit public download root",
            "url": "https://public.bybit.com/",
            "official": True,
            "public_reproducible": live_public_download["error"] is None,
            "historical_timepoint_support": False,
            "fields": [],
            "covers_months": False,
            "can_verify_symbol_month": False,
            "reason": "no versioned instrument-status snapshot with symbol lifecycle fields was identified in this audit",
            "live_request": live_public_download,
        },
        {
            "source_id": "internet_archive_cdx_candidate",
            "name": "Internet Archive CDX candidate",
            "url": "https://web.archive.org/cdx/search/cdx",
            "official": False,
            "public_reproducible": live_archive["error"] is None,
            "historical_timepoint_support": False,
            "fields": [],
            "covers_months": False,
            "can_verify_symbol_month": False,
            "reason": "no audited immutable snapshot body was obtained and no current snapshot is promoted to historical proof",
            "live_request": live_archive,
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Historical Bybit source and funding timeline evidence audit")
    parser.add_argument("--input", type=Path, default=Path("reports/crypto-backtest/multitier-mechanism-discovery-v1/universe-expansion-audit-v1"))
    parser.add_argument("--output", type=Path, default=Path("reports/crypto-backtest/multitier-mechanism-discovery-v1/historical-evidence-audit-v1"))
    parser.add_argument("--network-timeout", type=int, default=8)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).isoformat()
    snapshot = json.loads((args.input / "current_instruments_snapshot.json").read_text(encoding="utf-8"))
    previous_funding = json.loads((args.input / "funding_api_capability_audit.json").read_text(encoding="utf-8"))

    inventory = build_source_inventory(snapshot, args.network_timeout)
    source_document = {"audit_version": AUDIT_VERSION, "generated_at_utc": generated, "sources": inventory}
    write_json(args.output / "historical_source_inventory.json", source_document)
    (args.output / "historical_source_inventory.sha256").write_text(canonical_sha256(source_document) + "\n", encoding="utf-8")

    decisions: list[dict[str, Any]] = []
    for entry in snapshot["selected"]:
        raw = entry["raw"]
        for month in MONTHS:
            rebalance = pd.Timestamp(f"{month}-01", tz="UTC")
            status, reason = decide_historical_eligibility(raw.get("launchTime"), rebalance, False)
            decisions.append({
                "symbol": entry["symbol"], "month": month, "rebalance_timestamp_utc": rebalance.isoformat(),
                "decision": status, "reason": reason, "launch_time_ms": raw.get("launchTime"),
                "source_chain": "bybit_v5_instruments_current", "strict_eligible": False,
            })
    decisions.sort(key=lambda row: (row["month"], row["symbol"]))
    decision_csv = args.output / "strict_eligibility_decision.csv"
    with decision_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(decisions[0])); writer.writeheader(); writer.writerows(decisions)
    (args.output / "strict_eligibility_decision.sha256").write_text(hashlib.sha256(decision_csv.read_bytes()).hexdigest() + "\n", encoding="utf-8")
    evidence_document = {
        "audit_version": AUDIT_VERSION, "generated_at_utc": generated,
        "decision_rule": "verified requires reproducible historical state, not current status or kline presence",
        "source_inventory_sha256": canonical_sha256(source_document), "decisions": decisions,
    }
    write_json(args.output / "historical_instrument_evidence.json", evidence_document)
    pd.DataFrame(decisions).to_csv(args.output / "historical_instrument_evidence.csv", index=False)

    traces, reconciliation = [], []
    funding_live_failure: str | None = None
    for index, contract in enumerate(previous_funding["contracts"]):
        live: dict[str, Any] | None = None
        if index == 0:
            live = fetch_funding_timeline(
                contract["symbol"], pd.Timestamp("2026-06-01", tz="UTC"), pd.Timestamp("2026-08-30", tz="UTC"), args.network_timeout
            )
            funding_live_failure = live["failure"]
        pages = contract.get("requests", [])
        progression_ok, progression_reason = validate_backward_page_trace(pages)
        legacy_observed_minutes = sorted({int(round(hours * 60)) for hours in contract.get("observed_intervals_hours", [])})
        if live and live["timestamps"]:
            observed_minutes = sorted({int(round((b - a) / 60_000)) for a, b in zip(live["timestamps"], live["timestamps"][1:])})
            classification, reason = classify_interval(contract.get("metadata_interval_minutes"), observed_minutes, live["failure"] is None)
            coverage_start = pd.to_datetime(live["timestamps"][0], unit="ms", utc=True).isoformat()
            coverage_end = pd.to_datetime(live["timestamps"][-1], unit="ms", utc=True).isoformat()
            evidence_status = "verified" if live["failure"] is None else "pending_evidence"
            pending_reason = live["failure"]
        else:
            observed_minutes = legacy_observed_minutes
            classification, reason = classify_interval(contract.get("metadata_interval_minutes"), observed_minutes, False)
            coverage_start, coverage_end = contract.get("coverage_start_utc"), contract.get("coverage_end_utc")
            evidence_status = "pending_evidence"
            pending_reason = "live funding request unavailable; only prior summarized hashes and intervals retained"
        trace = {
            "symbol": contract["symbol"], "source": "prior_audit_public_response_hashes_only",
            "pages": pages, "page_count": len(pages), "end_time_progression_ok": progression_ok,
            "end_time_progression_reason": progression_reason,
            "raw_response_bodies_available": False,
            "live_refetch": live if live else {"not_attempted_per_symbol": True, "shared_first_request_failure": funding_live_failure},
        }
        traces.append(trace)
        reconciliation.append({
            "symbol": contract["symbol"], "metadata_interval_minutes": contract.get("metadata_interval_minutes"),
            "observed_interval_minutes": json.dumps(observed_minutes), "coverage_start_utc": coverage_start,
            "coverage_end_utc": coverage_end, "research_window_complete": bool(coverage_start and coverage_start <= "2026-06-01T00:00:00+00:00" and coverage_end and coverage_end >= "2026-08-29T23:59:59+00:00"),
            "legacy_gap_count": contract.get("gap_count"), "pagination_progression": progression_reason,
            "interval_classification": classification, "classification_reason": reason,
            "evidence_status": evidence_status, "pending_reason": pending_reason,
        })
    trace_document = {"audit_version": AUDIT_VERSION, "generated_at_utc": generated, "scope": previous_funding.get("scope"), "traces": traces}
    reconciliation_document = {"audit_version": AUDIT_VERSION, "generated_at_utc": generated, "research_window_utc": ["2026-06-01T00:00:00+00:00", "2026-08-29T23:59:59+00:00"], "contracts": reconciliation}
    write_json(args.output / "funding_pagination_trace.json", trace_document)
    write_json(args.output / "funding_timeline_reconciliation.json", reconciliation_document)
    pd.DataFrame(reconciliation).to_csv(args.output / "funding_timeline_reconciliation.csv", index=False)
    (args.output / "funding_timeline_reconciliation.sha256").write_text(canonical_sha256(reconciliation_document) + "\n", encoding="utf-8")

    counts = pd.Series([row["decision"] for row in decisions]).value_counts().to_dict()
    report = [
        "# HISTORICAL_EVIDENCE_AUDIT_V1", "",
        f"- [COMPUTED] Decisions: {counts}.",
        "- [KNOWN] No current status field, kline presence, or prior summary is upgraded to historical verified status.",
        "- [COMPUTED] No source in this audit can verify symbol-month trading eligibility; strict historical universe remains empty.",
        "- [COMPUTED] Funding prior-page hashes demonstrate bounded backward endTime requests, but retained records are insufficient for a complete timeline reconciliation.",
        "- [KNOWN] historical_evidence_not_ready.",
    ]
    (args.output / "HISTORICAL_EVIDENCE_AUDIT_V1.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"status": "historical_evidence_not_ready", "sources": len(inventory), "decisions": len(decisions), "funding_contracts": len(reconciliation), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
