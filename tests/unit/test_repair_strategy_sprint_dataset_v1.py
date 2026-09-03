import hashlib

import pandas as pd
import requests

import research.crypto_backtest.repair_strategy_sprint_dataset_v1 as repair
from research.crypto_backtest.repair_strategy_sprint_dataset_v1 import (
    local_candidates,
    missing_segments,
    validate_frame,
    merge_sources,
    quality_for_stage,
)


def frame(points):
    return pd.DataFrame({"timestamp": pd.to_datetime(points, utc=True), "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 1.0, "turnover": 1.0})


def test_identical_overlap_is_deduplicated():
    merged, status = merge_sources([frame(["2026-07-01T00:00:00Z"]), frame(["2026-07-01T00:00:00Z"])])
    assert status == "quality_pass" and len(merged) == 1


def test_conflicting_overlap_stops_source_merge():
    left, right = frame(["2026-07-01T00:00:00Z"]), frame(["2026-07-01T00:00:00Z"]); right.loc[0, "close"] = 2.0
    merged, status = merge_sources([left, right])
    assert merged is None and status == "conflicting_local_source"


def test_validation_first_day_gap_is_exactly_96_bars():
    bars = pd.date_range("2026-07-02T00:00:00Z", "2026-07-31T23:45:00Z", freq="15min")
    result = quality_for_stage(frame(bars), "validation")
    assert result["missing_15m_bars"] == 96 and not result["quality_pass"]
    assert missing_segments(frame(bars), "validation") == [(pd.Timestamp("2026-07-01T00:00:00Z"), pd.Timestamp("2026-07-01T23:45:00Z"))]


def test_invalid_ohlc_is_rejected():
    bad = frame(["2026-06-01T00:00:00Z"]); bad.loc[0, "high"] = 0.5
    assert validate_frame(bad)[0] is False


def test_network_timeout_is_not_reclassified_as_missing_data(monkeypatch, tmp_path):
    monkeypatch.setattr(repair.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(requests.ReadTimeout()))
    monkeypatch.setattr(repair.time, "sleep", lambda *args: None)
    frame, pages = repair.fetch_missing_symbol(
        "HYPEUSDT", pd.Timestamp("2026-06-01T00:00:00Z"), pd.Timestamp("2026-06-01T00:00:00Z"), tmp_path / "raw_kline_pages"
    )
    assert frame is None and pages[0]["error"] and pages[0]["raw_path"] is None
    assert (tmp_path / "raw_kline_pages").is_dir()


def test_successful_raw_page_has_recomputable_hash(monkeypatch, tmp_path):
    body = b'{"retCode":0,"result":{"list":[]}}'

    class Response:
        status_code = 200
        content = body

        def raise_for_status(self):
            return None

        def json(self):
            return {"retCode": 0, "result": {"list": []}}

    monkeypatch.setattr(repair.requests, "get", lambda *args, **kwargs: Response())
    result = repair.request_page("HYPEUSDT", 1, 2, tmp_path, 1)
    raw = tmp_path / "HYPEUSDT" / "HYPEUSDT-page-0001-start-1-end-2.json"
    assert raw.read_bytes() == body
    assert result["sha256"] == hashlib.sha256(body).hexdigest()


def test_network_kline_values_are_normalized_to_numeric(monkeypatch, tmp_path):
    body = b'{"retCode":0,"result":{"list":[["1780272000000","1","1.1","0.9","1","2","3"]]}}'

    class Response:
        status_code = 200
        content = body

        def raise_for_status(self):
            return None

        def json(self):
            return {"retCode": 0, "result": {"list": [["1780272000000", "1", "1.1", "0.9", "1", "2", "3"]]}}

    monkeypatch.setattr(repair.requests, "get", lambda *args, **kwargs: Response())
    frame, _ = repair.fetch_missing_symbol("HYPEUSDT", pd.Timestamp("2026-06-01T00:00:00Z"), pd.Timestamp("2026-06-01T00:00:00Z"), tmp_path)
    assert frame is not None and frame.dtypes["open"].kind in "if"


def test_chunked_symbol_interval_filename_is_discovered(tmp_path):
    source = tmp_path / "HYPEUSDT-20260601-15m.csv.gz"
    source.write_bytes(b"fixture")
    assert local_candidates(tmp_path, "HYPEUSDT") == [source]
