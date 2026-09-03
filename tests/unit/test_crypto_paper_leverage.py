from app.services.crypto.paper_leverage import build_position, close_position, mark_position


def test_isolated_long_pnl_and_liquidation_price():
    position = build_position(symbol="BTCUSDT", side="long", quantity=0.1, entry_price=100000, leverage=10)
    marked = mark_position(position, 101000)
    assert round(marked["unrealized_pnl"], 2) == 100.0
    assert round(marked["roi_pct"], 2) == 10.0
    assert round(position["liquidation_price"], 2) == 90500.0
    assert marked["liquidation_triggered"] is False


def test_short_liquidation_and_net_close_pnl():
    position = build_position(symbol="ETHUSDT", side="short", quantity=1, entry_price=2000, leverage=5)
    closed = close_position(position, 1900)
    assert closed["status"] == "closed"
    assert closed["realized_pnl"] == 100.0
    assert closed["net_pnl"] < closed["realized_pnl"]

    liquidated = mark_position(position, position["liquidation_price"] + 1)
    assert liquidated["liquidation_triggered"] is True
