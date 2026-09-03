from research.crypto_backtest.reacquire_bybit_historical_evidence_v1 import (
    classify_timeline,
    historical_decision,
    interval_summary,
    next_end_time,
    save_raw,
)
import pandas as pd


def test_timeout_classification_is_not_source_absence():
    assert classify_timeline(240, [], False, True, False)[0] == "network_evidence_unavailable"


def test_current_state_without_time_bound_evidence_is_not_verified():
    assert historical_decision("1700000000000", pd.Timestamp("2026-06-01T00:00:00Z"), False)[0] == "unverifiable"


def test_late_launch_is_ineligible():
    assert historical_decision("1782864000000", pd.Timestamp("2026-06-01T00:00:00Z"), False)[0] == "ineligible"


def test_backward_cursor_and_empty_page_are_explicit():
    assert next_end_time([100, 200], 300) == (99, "continue")
    assert next_end_time([], 300) == (None, "empty_page")


def test_raw_page_sha256_is_recomputable(tmp_path):
    body = b'{"retCode":0}'
    path = tmp_path / "page.json"
    sha = save_raw(path, body)
    import hashlib
    assert sha == hashlib.sha256(path.read_bytes()).hexdigest()


def test_mixed_2z_intervals_remain_unresolved():
    stamps = [0, 60 * 60_000, 5 * 60 * 60_000]
    assert classify_timeline(240, interval_summary(stamps), True, False, False)[0] == "unresolved"


def test_prior_summary_without_raw_pages_is_not_reproducible_trace():
    assert classify_timeline(240, interval_summary([0, 240 * 60_000]), False, False, False)[0] == "not_reproducible_prior_trace"
