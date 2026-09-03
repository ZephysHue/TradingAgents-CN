import pandas as pd

from research.crypto_backtest.census_multitier_universe_v1 import classify_tiers, maximum_missing_run, quality_row, tier_positions


def test_lookback_window_excludes_reconstitution_month():
    month = pd.Timestamp("2026-08-01", tz="UTC")
    index = pd.date_range(month - pd.Timedelta(days=30), month + pd.Timedelta(days=1), freq="15min", inclusive="left", tz="UTC")
    frame = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0, "turnover": 1.0}, index=index)
    row = quality_row("AAAUSDT", month, frame, {"settleCoin": "USDT", "contractType": "LinearPerpetual", "status": "Trading"}, "2026-08-31T00:00:00+00:00")
    assert row["lookback_end_exclusive_utc"] == month.isoformat()
    assert row["observed_15m_bars"] == 30 * 96


def test_missing_run_is_not_silently_filled():
    expected = pd.date_range("2026-01-01", periods=12, freq="15min", tz="UTC")
    observed = expected.delete([3, 4, 5, 6, 7])
    assert maximum_missing_run(observed, expected) == 5


def test_inward_percentile_bands_and_insufficient_tier():
    assert tier_positions(100)["mid"] == list(range(45, 56))
    eligible = [{"symbol": f"S{index:02}USDT", "turnover_30d": float(100 - index)} for index in range(1, 61)]
    tiers = classify_tiers(eligible)
    assert len(tiers["hot"]) == 10
    assert len(tiers["mid"]) == 7
    assert len(tiers["low"]) == 7
    assert len(classify_tiers(eligible[:6])["mid"]) < 5
