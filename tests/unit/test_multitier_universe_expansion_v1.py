import pandas as pd

from research.crypto_backtest import audit_multitier_universe_expansion_v1 as audit
from research.crypto_backtest.audit_multitier_universe_expansion_v1 import historical_status, tier_summary


def test_current_snapshot_never_verifies_historical_status():
    state, reason = historical_status({"launchTime": "1704067200000"}, pd.Timestamp("2026-06-01", tz="UTC"))
    assert state == "unverifiable"
    assert "historical_status" in reason


def test_launch_after_month_is_ineligible():
    state, reason = historical_status({"launchTime": "1782864000000"}, pd.Timestamp("2026-06-01", tz="UTC"))
    assert state == "ineligible"
    assert reason == "launch_time_not_before_reconstitution"


def test_strict_and_provisional_are_separate():
    rows = [{"month": "2026-06", "symbol": "A", "turnover_30d": 2.0, "strict_universe": True, "provisional_universe": False}, {"month": "2026-06", "symbol": "B", "turnover_30d": 1.0, "strict_universe": False, "provisional_universe": True}]
    _, strict = tier_summary(rows, "strict_universe")
    _, provisional = tier_summary(rows, "provisional_universe")
    assert strict["months"][0]["eligible_contracts"] == 1
    assert provisional["months"][0]["eligible_contracts"] == 1


def test_dynamic_filter_accepts_only_required_current_fields(monkeypatch):
    items = [
        {"symbol": "OKUSDT", "settleCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading"},
        {"symbol": "BADUSD", "settleCoin": "USD", "contractType": "LinearPerpetual", "status": "Trading"},
        {"symbol": "BADTYPE", "settleCoin": "USDT", "contractType": "LinearFutures", "status": "Trading"},
        {"symbol": "BADSTATUS", "settleCoin": "USDT", "contractType": "LinearPerpetual", "status": "Paused"},
    ]
    monkeypatch.setattr(audit, "get_json", lambda path, params: ({"list": items, "nextPageCursor": ""}, "hash"))
    selected, excluded, pages = audit.current_instruments()
    assert [entry["symbol"] for entry in selected] == ["OKUSDT"]
    assert len(excluded) == 3 and pages[0]["response_sha256"] == "hash"


def test_funding_pagination_marks_incomplete_range_pending(monkeypatch):
    stamps = [int(pd.Timestamp("2026-06-01", tz="UTC").timestamp() * 1000), int(pd.Timestamp("2026-06-01 04:00", tz="UTC").timestamp() * 1000)]
    monkeypatch.setattr(audit, "get_json", lambda path, params: ({"list": [{"fundingRateTimestamp": str(value)} for value in stamps]}, "hash"))
    row = audit.funding_probe("AAAUSDT", {"fundingInterval": 240}, pd.Timestamp("2026-05-01", tz="UTC"), pd.Timestamp("2026-08-01", tz="UTC"))
    assert row["status"] == "pending_evidence"
    assert row["observed_intervals_hours"] == [4.0]
