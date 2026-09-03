import pandas as pd

from research.crypto_backtest.audit_bybit_historical_instrument_evidence_v1 import (
    canonical_sha256,
    classify_interval,
    decide_historical_eligibility,
    normalise_funding_timestamps,
    validate_backward_page_trace,
)


def test_current_state_cannot_be_verified_without_historical_snapshot():
    result, _ = decide_historical_eligibility("1704067200000", pd.Timestamp("2026-06-01", tz="UTC"), False)
    assert result == "unverifiable"


def test_late_launch_is_ineligible():
    result, _ = decide_historical_eligibility("1782864000000", pd.Timestamp("2026-06-01", tz="UTC"), False)
    assert result == "ineligible"


def test_backward_funding_pages_must_strictly_decrease():
    assert validate_backward_page_trace([{"params": {"endTime": 300}}, {"params": {"endTime": 200}}]) == (True, "strictly_decreasing")
    assert validate_backward_page_trace([{"params": {"endTime": 300}}, {"params": {"endTime": 300}}]) == (False, "end_time_not_strictly_decreasing")


def test_incomplete_trace_is_not_metadata_confirmation():
    assert classify_interval(240, [240], False) == ("pagination_gap", "raw_pages_or_full_trace_unavailable")
    assert classify_interval(240, [240], True) == ("consistent", "metadata_matches_observed")


def test_funding_timestamps_are_deduplicated_and_sorted():
    assert normalise_funding_timestamps([300, 100, 300, 200]) == [100, 200, 300]


def test_canonical_sha_is_repeatable():
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})
