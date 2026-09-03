"""Bybit V5 linear-perpetual public market-data client.

This module is read-only: it deliberately contains no account, order, or
leverage-trading API.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx


class BybitMarketDataError(RuntimeError):
    """Raised when a Bybit public market-data request fails."""


class BybitFuturesClient:
    BASE_URL = "https://api.bybit.com"
    INTERVALS = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720", "1d": "D"}
    SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.getenv("BYBIT_FUTURES_REST_URL") or self.BASE_URL).rstrip("/")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            # The desktop compose stack may contain a stale Binance-only proxy.
            # Bybit is intentionally reached directly; changing proxy state must
            # not make its public read-only feed unavailable.
            async with httpx.AsyncClient(base_url=self.base_url, timeout=12.0, trust_env=False) as client:
                response = await client.get(path, params=params)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BybitMarketDataError(f"Bybit request failed: {exc}") from exc
        if payload.get("retCode") != 0:
            raise BybitMarketDataError(payload.get("retMsg", "Bybit API error"))
        return payload["result"]

    @classmethod
    def normalize_symbol(cls, symbol: str) -> str:
        normalized = symbol.strip().upper().replace("/", "").replace("-", "")
        if not cls.SYMBOL_RE.fullmatch(normalized):
            raise ValueError("symbol must be an uppercase Bybit linear symbol such as BTCUSDT")
        return normalized

    @staticmethod
    def _utc(ms: int | float | str) -> str:
        return datetime.fromtimestamp(float(ms) / 1000, tz=timezone.utc).isoformat()

    async def status(self) -> dict[str, Any]:
        payload = await self._get("/v5/market/time")
        return {"exchange": "bybit", "market": "linear_perpetual", "server_time": int(payload["timeNano"]) // 1_000_000}

    async def symbols(self) -> list[dict[str, Any]]:
        payload = await self._get("/v5/market/instruments-info", {"category": "linear", "limit": 1000})
        return [
            {
                "symbol": item["symbol"],
                "base_asset": item["baseCoin"],
                "quote_asset": item["quoteCoin"],
                "status": item["status"],
                "contract_type": item["contractType"],
                "price_precision": item.get("priceScale"),
                "quantity_precision": item.get("lotSizeFilter", {}).get("qtyStep"),
            }
            for item in payload.get("list", [])
            if item.get("status") == "Trading" and item.get("quoteCoin") == "USDT" and item.get("contractType") == "LinearPerpetual"
        ]

    async def quote(self, symbol: str) -> dict[str, Any]:
        symbol = self.normalize_symbol(symbol)
        payload = await self._get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
        if not payload.get("list"):
            raise BybitMarketDataError(f"unknown or inactive linear symbol: {symbol}")
        ticker = payload["list"][0]
        return {
            "symbol": symbol,
            "last_price": float(ticker["lastPrice"]),
            "price_change_pct_24h": float(ticker["price24hPcnt"]) * 100,
            "high_price_24h": float(ticker["highPrice24h"]),
            "low_price_24h": float(ticker["lowPrice24h"]),
            "volume_24h": float(ticker["volume24h"]),
            "quote_volume_24h": float(ticker["turnover24h"]),
            "mark_price": float(ticker["markPrice"]),
            "index_price": float(ticker["indexPrice"]),
            "last_funding_rate": float(ticker.get("fundingRate") or 0),
            "next_funding_time": self._utc(ticker["nextFundingTime"]) if ticker.get("nextFundingTime") else None,
            "open_interest": float(ticker.get("openInterest") or 0),
            "source": "bybit_linear_perpetual",
            "source_updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def klines(self, symbol: str, interval: str = "1m", limit: int = 240) -> list[dict[str, Any]]:
        symbol = self.normalize_symbol(symbol)
        if interval not in self.INTERVALS:
            raise ValueError(f"unsupported interval: {interval}")
        limit = max(10, min(limit, 1000))
        payload = await self._get("/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": self.INTERVALS[interval], "limit": limit})
        rows = list(reversed(payload.get("list", [])))
        interval_ms = {"D": 86_400_000}.get(self.INTERVALS[interval], int(self.INTERVALS[interval]) * 60_000)
        return [
            {
                "symbol": symbol,
                "interval": interval,
                "open_time": self._utc(row[0]),
                "close_time": self._utc(int(row[0]) + interval_ms - 1),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "quote_volume": float(row[6]),
                "trade_count": None,
                "taker_buy_volume": None,
            }
            for row in rows
        ]

    async def volatility(self, symbol: str, interval: str = "1m", limit: int = 240) -> dict[str, Any]:
        bars = await self.klines(symbol, interval, limit)
        closes = [bar["close"] for bar in bars]
        returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]
        true_ranges = [max(bar["high"] - bar["low"], abs(bar["high"] - (bars[index - 1]["close"] if index else bar["open"])), abs(bar["low"] - (bars[index - 1]["close"] if index else bar["open"]))) for index, bar in enumerate(bars)]
        mean_close = sum(closes) / len(closes)
        mean_volume = sum(bar["volume"] for bar in bars[:-1]) / max(len(bars) - 1, 1)
        peak, max_drawdown = closes[0], 0.0
        for close in closes:
            peak = max(peak, close); max_drawdown = min(max_drawdown, close / peak - 1)
        return {"symbol": symbol.upper(), "interval": interval, "bars": len(bars), "realized_volatility_annualized": math.sqrt(sum(r * r for r in returns[-20:]) / max(len(returns[-20:]), 1)) * math.sqrt(365 * 24 * 60), "atr_pct": (sum(true_ranges[-14:]) / max(len(true_ranges[-14:]), 1)) / mean_close, "momentum_5_bars_pct": closes[-1] / closes[-6] - 1 if len(closes) > 5 else 0.0, "momentum_15_bars_pct": closes[-1] / closes[-16] - 1 if len(closes) > 15 else 0.0, "mean_bar_range_pct": sum((bar["high"] - bar["low"]) / bar["close"] for bar in bars) / len(bars), "volume_ratio": bars[-1]["volume"] / mean_volume if mean_volume else 0.0, "max_drawdown": max_drawdown, "latest_close": closes[-1], "source_updated_at": bars[-1]["close_time"]}

    async def snapshot(self, symbol: str, interval: str = "1m", limit: int = 240) -> dict[str, Any]:
        quote, volatility = await asyncio.gather(self.quote(symbol), self.volatility(symbol, interval, limit))
        return {"quote": quote, "volatility": volatility}
