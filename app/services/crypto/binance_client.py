"""Binance USDⓈ-M public market-data client.

This module intentionally contains no account, order, or leverage-trading API.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx


class BinanceMarketDataError(RuntimeError):
    """Raised when Binance public market data cannot be fetched."""


class BinanceFuturesClient:
    BASE_URL = "https://fapi.binance.com"
    INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"}
    SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.getenv("BINANCE_FUTURES_REST_URL") or self.BASE_URL).rstrip("/")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=12.0) as client:
                response = await client.get(path, params=params)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BinanceMarketDataError(f"Binance request failed: {exc}") from exc
        if isinstance(payload, dict) and payload.get("code", 0) < 0:
            raise BinanceMarketDataError(payload.get("msg", "Binance API error"))
        return payload

    @classmethod
    def normalize_symbol(cls, symbol: str) -> str:
        normalized = symbol.strip().upper().replace("/", "")
        if not cls.SYMBOL_RE.fullmatch(normalized):
            raise ValueError("symbol must be an uppercase Binance symbol such as BTCUSDT")
        return normalized

    @staticmethod
    def _utc(ms: int | float) -> str:
        return datetime.fromtimestamp(float(ms) / 1000, tz=timezone.utc).isoformat()

    async def status(self) -> dict[str, Any]:
        payload = await self._get("/fapi/v1/time")
        return {"exchange": "binance", "market": "usdt_m_futures", "server_time": payload["serverTime"]}

    async def symbols(self) -> list[dict[str, Any]]:
        payload = await self._get("/fapi/v1/exchangeInfo")
        return [
            {
                "symbol": item["symbol"],
                "base_asset": item["baseAsset"],
                "quote_asset": item["quoteAsset"],
                "status": item["status"],
                "contract_type": item["contractType"],
                "price_precision": item.get("pricePrecision"),
                "quantity_precision": item.get("quantityPrecision"),
            }
            for item in payload.get("symbols", [])
            if item.get("status") == "TRADING" and item.get("quoteAsset") == "USDT"
        ]

    async def quote(self, symbol: str) -> dict[str, Any]:
        symbol = self.normalize_symbol(symbol)
        ticker, premium, interest = await asyncio.gather(
            self._get("/fapi/v1/ticker/24hr", {"symbol": symbol}),
            self._get("/fapi/v1/premiumIndex", {"symbol": symbol}),
            self._get("/fapi/v1/openInterest", {"symbol": symbol}),
        )
        return {
            "symbol": symbol,
            "last_price": float(ticker["lastPrice"]),
            "price_change_pct_24h": float(ticker["priceChangePercent"]),
            "high_price_24h": float(ticker["highPrice"]),
            "low_price_24h": float(ticker["lowPrice"]),
            "volume_24h": float(ticker["volume"]),
            "quote_volume_24h": float(ticker["quoteVolume"]),
            "mark_price": float(premium["markPrice"]),
            "index_price": float(premium["indexPrice"]),
            "last_funding_rate": float(premium["lastFundingRate"]),
            "next_funding_time": self._utc(premium["nextFundingTime"]),
            "open_interest": float(interest["openInterest"]),
            "source": "binance_usdt_m_futures",
            "source_updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def klines(self, symbol: str, interval: str = "1m", limit: int = 240) -> list[dict[str, Any]]:
        symbol = self.normalize_symbol(symbol)
        if interval not in self.INTERVALS:
            raise ValueError(f"unsupported interval: {interval}")
        limit = max(10, min(limit, 1500))
        payload = await self._get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        return [
            {
                "symbol": symbol,
                "interval": interval,
                "open_time": self._utc(row[0]),
                "close_time": self._utc(row[6]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "quote_volume": float(row[7]),
                "trade_count": int(row[8]),
                "taker_buy_volume": float(row[9]),
            }
            for row in payload
        ]

    async def volatility(self, symbol: str, interval: str = "1m", limit: int = 240) -> dict[str, Any]:
        bars = await self.klines(symbol, interval, limit)
        closes = [bar["close"] for bar in bars]
        returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]
        true_ranges = []
        for index, bar in enumerate(bars):
            previous_close = bars[index - 1]["close"] if index else bar["open"]
            true_ranges.append(max(bar["high"] - bar["low"], abs(bar["high"] - previous_close), abs(bar["low"] - previous_close)))
        mean_close = sum(closes) / len(closes)
        recent = returns[-20:]
        mean_volume = sum(bar["volume"] for bar in bars[:-1]) / max(len(bars) - 1, 1)
        peak = closes[0]
        max_drawdown = 0.0
        for close in closes:
            peak = max(peak, close)
            max_drawdown = min(max_drawdown, close / peak - 1)
        return {
            "symbol": symbol.upper(),
            "interval": interval,
            "bars": len(bars),
            "realized_volatility_annualized": math.sqrt(sum(r * r for r in recent) / max(len(recent), 1)) * math.sqrt(365 * 24 * 60),
            "atr_pct": (sum(true_ranges[-14:]) / max(len(true_ranges[-14:]), 1)) / mean_close,
            "momentum_5_bars_pct": closes[-1] / closes[-6] - 1 if len(closes) > 5 else 0.0,
            "momentum_15_bars_pct": closes[-1] / closes[-16] - 1 if len(closes) > 15 else 0.0,
            "mean_bar_range_pct": sum((bar["high"] - bar["low"]) / bar["close"] for bar in bars) / len(bars),
            "volume_ratio": bars[-1]["volume"] / mean_volume if mean_volume else 0.0,
            "max_drawdown": max_drawdown,
            "latest_close": closes[-1],
            "source_updated_at": bars[-1]["close_time"],
        }

    async def snapshot(self, symbol: str, interval: str = "1m", limit: int = 240) -> dict[str, Any]:
        quote, volatility = await asyncio.gather(self.quote(symbol), self.volatility(symbol, interval, limit))
        return {"quote": quote, "volatility": volatility}
