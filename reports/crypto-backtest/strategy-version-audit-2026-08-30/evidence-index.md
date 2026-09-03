# Evidence Index

## Shared data and settlement evidence

- [KNOWN] `research/crypto_backtest/data/manifest.json` — Binance COIN-M BTCUSD_PERP 15m archive list and SHA256 identities.
- [KNOWN] `research/crypto_backtest/coin_m_engine.py` — 100 USD inverse contract PnL, BTC fees, adverse fills, and integer quantity primitives.
- [KNOWN] `research/crypto_backtest/test_coin_m_engine.py` — unit-test evidence for inverse PnL directions.

## V1

- [KNOWN] `research/crypto_backtest/backtest.py` — strategy and data-alignment implementation.
- [KNOWN] `reports/crypto-backtest/BASELINE_trades.csv` — fixed 1,858-entry baseline record.
- [KNOWN] `research/crypto_backtest/audit_corrected_baseline.py` and `reports/crypto-backtest/baseline-audit-v2/audit_summary.json` — A/B/C/D costs, accounting identity, signal consistency, inverse settlement audit.
- [KNOWN] `reports/crypto-backtest/entry-time-predictive-diagnostic/entry_time_predictive_summary.json` — entry-time feature diagnostic; no stable candidate.

## V2

- [KNOWN] `research/crypto_backtest/backtest_v2_structural_reexpansion.py` — frozen V2 v0.1 implementation.
- [KNOWN] `reports/crypto-backtest/v2-structural-reexpansion-v0.1/v2_baseline_summary.json` — results, bootstrap, tail and leakage audit.

## V3

- [KNOWN] `research/crypto_backtest/backtest_v3_compression_breakout_expansion.py` — frozen V3 v0.1 implementation.
- [KNOWN] `reports/crypto-backtest/v3-compression-breakout-expansion-v0.1/v3_baseline_summary.json` — raw/executable/executed counts, costs, bootstrap, leakage and ledger audit.

## Scope boundary

- [KNOWN] `reports/crypto-backtest/market-edge-discovery-v1/development_discovery.json` and `candidate_registry.json` — zero frozen candidates for the stated forward-return route. This is not evidence that all strategy variants universally fail.
