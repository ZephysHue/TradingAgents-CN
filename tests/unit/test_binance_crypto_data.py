import asyncio

from app.services.crypto.binance_client import BinanceFuturesClient


def test_binance_symbol_normalization():
    assert BinanceFuturesClient.normalize_symbol(" btc/usdt ") == "BTCUSDT"


def test_binance_kline_mapping_and_volatility():
    client = BinanceFuturesClient()
    raw = [
        [i * 60_000, str(100 + i), str(102 + i), str(99 + i), str(101 + i), "10", i * 60_000 + 59_999, "1000", 5, "4", "400", "0"]
        for i in range(30)
    ]

    async def fake_get(path, params=None):
        return raw

    client._get = fake_get
    bars = asyncio.run(client.klines("BTCUSDT", "1m", 30))
    metrics = asyncio.run(client.volatility("BTCUSDT", "1m", 30))

    assert len(bars) == 30
    assert bars[-1]["close"] == 130.0
    assert metrics["bars"] == 30
    assert metrics["atr_pct"] > 0
    assert metrics["volume_ratio"] == 1.0
