"""Download Binance public futures klines with a manifest and checksum metadata."""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import sys
import zipfile
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import requests


ARCHIVE = "https://data.binance.vision/data/futures/{market}/monthly/klines/{symbol}/15m/{symbol}-15m-{ym}.zip"


def months(start: date, end: date):
    current = date(start.year, start.month, 1)
    while current <= end:
        yield current.strftime("%Y-%m")
        current = date(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1)


def fetch(url: str, session: requests.Session):
    response = session.get(url, timeout=45)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--output", default="research/crypto_backtest/data")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.proxies.update({k.lower(): v for k, v in os.environ.items() if k.lower() in {"http_proxy", "https_proxy"}})

    records = []
    selected = None
    for market, symbol in (("cm", "BTCUSD_PERP"), ("um", "BTCUSDT")):
        found = 0
        for ym in months(start, end):
            payload = fetch(ARCHIVE.format(market=market, symbol=symbol, ym=ym), session)
            if payload is None:
                continue
            selected = (market, symbol)
            target = root / market / symbol
            target.mkdir(parents=True, exist_ok=True)
            path = target / f"{symbol}-15m-{ym}.zip"
            path.write_bytes(payload)
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                csv_names = [name for name in archive.namelist() if name.endswith(".csv")]
                if not csv_names:
                    raise RuntimeError(f"archive has no csv: {path}")
            records.append({"market": market, "symbol": symbol, "month": ym, "path": str(path), "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)})
            found += 1
        if found:
            break
    if not records:
        print("No Binance archive files downloaded", file=sys.stderr)
        return 2
    (root / "manifest.json").write_text(json.dumps({"source": "Binance public data archive", "requested_start": args.start, "requested_end": args.end, "selected_market": selected[0], "selected_symbol": selected[1], "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"selected_market": selected[0], "selected_symbol": selected[1], "months": len(records), "manifest": str(root / 'manifest.json')}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
