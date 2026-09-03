from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.services.crypto.paper_automation import AUDIT, FILLS, ORDERS, PaperAutomationService, _strategy_signal
from app.services.crypto.paper_registry import StrategyRegistryEntry


class Result:
    async def __await__(self):
        yield


class Cursor:
    def __init__(self, docs): self.docs = docs
    def __aiter__(self):
        self.index = 0
        return self
    async def __anext__(self):
        if self.index >= len(self.docs): raise StopAsyncIteration
        value = self.docs[self.index]; self.index += 1; return deepcopy(value)


class Collection:
    def __init__(self): self.docs = []
    async def create_index(self, *args, **kwargs): return "ok"
    def find(self, query):
        def match(doc):
            for key, value in query.items():
                if isinstance(value, dict) and "$gt" in value:
                    if not doc.get(key, 0) > value["$gt"]: return False
                elif doc.get(key) != value:
                    return False
            return True
        return Cursor([doc for doc in self.docs if match(doc)])
    async def find_one(self, query):
        docs = [doc async for doc in self.find(query)]
        return docs[0] if docs else None
    async def insert_one(self, doc): self.docs.append(deepcopy(doc))
    async def replace_one(self, query, doc, upsert=False):
        for index, old in enumerate(self.docs):
            if all(old.get(k) == v for k, v in query.items()): self.docs[index] = deepcopy(doc); return
        if upsert: self.docs.append(deepcopy(doc))


class DB:
    def __init__(self): self.collections = {}
    def __getitem__(self, key): return self.collections.setdefault(key, Collection())


class Provider:
    def __init__(self, bars): self.bars = bars
    async def klines(self, symbol, interval, limit): return self.bars


def bars():
    start = datetime.now(timezone.utc) - timedelta(minutes=15 * 301)
    values = [100.0] * 297 + [99.0, 102.0, 102.0]
    return [{"open_time": (start + timedelta(minutes=15 * i)).isoformat(), "close_time": (start + timedelta(minutes=15 * (i + 1)) - timedelta(seconds=1)).isoformat(), "open": value, "close": value} for i, value in enumerate(values)]


def entry():
    return StrategyRegistryEntry("s1", "c1", "paper_champion", ("range", "trend_up"), ("BTCUSDT",), "15m", "sha256:x", "2026-09-01T00:00:00Z", 0.2, 0.1)


def test_sma_signal_crosses_on_closed_signal_bar():
    entry_signal, exit_signal = _strategy_signal({"kind": "sma_cross", "fast_window": 2, "slow_window": 3}, __import__("pandas").Series([100, 100, 100, 99, 102]))
    assert entry_signal and not exit_signal


def test_no_champion_creates_no_orders_and_audits():
    db = DB(); result = asyncio.run(PaperAutomationService(db, Provider(bars())).run_once())
    assert result == {"champions": 0, "processed": 0, "orders": 0, "skipped": 0}
    assert db[AUDIT].docs[0]["event_type"] == "no_active_paper_champion"


def test_champion_processes_only_once_and_persists_paper_fill():
    db = DB(); service = PaperAutomationService(db, Provider(bars()))
    async def run():
        await service.ensure_indexes()
        await service.upsert_registry(entry(), {"kind": "sma_cross", "fast_window": 2, "slow_window": 3})
        return await service.run_once(), await service.run_once()
    first, second = asyncio.run(run())
    assert first["orders"] == 1 and second["orders"] == 0
    assert db[ORDERS].docs[0]["paper_only"] is True
    assert db[FILLS].docs[0]["paper_only"] is True
