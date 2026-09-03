import pandas as pd
import pytest

from app.services.crypto.market_context import classify_regime
from app.services.crypto.paper_broker import create_order, fill_order


def test_regime_is_deterministic_and_does_not_need_llm():
    close = pd.Series([100.0] * 24 + [102.0])
    assert classify_regime(close)["deterministic_label"] == "trend_up"


def test_high_volatility_precedes_trend_label():
    close = pd.Series([100.0, 105.0] * 13)
    assert classify_regime(close)["deterministic_label"] == "high_volatility"


def test_paper_fill_applies_directional_slippage_and_fee():
    order = create_order(strategy_id="s1", symbol="btcusdt", side="buy", quantity=2, eligible_execution_bar_utc="2026-09-03T00:15:00Z")
    fill = fill_order(order, 100.0, fee_rate=0.001, slippage=0.002, filled_at_utc="2026-09-03T00:15:00Z")
    assert fill["fill_price"] == 100.2
    assert fill["fee"] == pytest.approx(0.2004)
    assert fill["paper_only"] is True
