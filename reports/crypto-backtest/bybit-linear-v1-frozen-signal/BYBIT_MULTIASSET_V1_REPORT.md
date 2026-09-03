# Bybit Linear Multi-Asset Frozen-V1 Run

## Scope

- [KNOWN] Market: Bybit V5 USDT linear perpetuals.
- [KNOWN] Symbols were frozen from a live 24-hour turnover snapshot: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `DOGEUSDT`, `ADAUSDT`.
- [KNOWN] Data interval: 15 minutes; common requested period: 2023-01-01 through 2026-08-29 UTC.
- [KNOWN] Signal rules are the existing frozen V1 baseline rules. They were not optimized or changed.
- [KNOWN] Settlement is a separate USDT-linear ledger, with 1% current-equity risk, 0.04% taker fee and 2 bp adverse slippage in Scenario D.
- [KNOWN] Funding is excluded because this run did not download verified Bybit funding history.

## Results

| Symbol | A trades | A expectancy R | A PF | D expectancy R | D PF | D net PnL USDT | D max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 1114 | 0.0449 | 1.0687 | -0.1353 | 0.7415 | -795.61 | -81.70% |
| ETHUSDT | 1176 | 0.0204 | 1.0157 | -0.1255 | 0.7874 | -790.77 | -81.68% |
| SOLUSDT | 1206 | 0.0945 | 1.1398 | -0.0147 | 0.9641 | -243.72 | -49.50% |
| XRPUSDT | 1111 | 0.0504 | 1.0630 | -0.0695 | 0.8744 | -577.93 | -63.02% |
| DOGEUSDT | 1158 | 0.0440 | 1.0496 | -0.0684 | 0.8810 | -588.10 | -60.30% |
| ADAUSDT | 980 | -0.0265 | 0.9481 | -0.1207 | 0.8112 | -717.03 | -73.63% |

## Conclusion

- [COMPUTED] Scenario A is positive for five of six symbols, but the gross advantage is thin.
- [COMPUTED] Scenario D is negative with PF below 1 for all six symbols. SOLUSDT is closest to break-even but remains negative after stated costs.
- [INFERRED] This cross-asset run does not support taking V1 into paper or live trading. It strengthens the existing conclusion that frozen V1 does not have sufficient cost-adjusted robustness.
- [KNOWN] This is not a portfolio test: every symbol has an independent USDT ledger. Do not aggregate the six ending equities as a tradable portfolio result.

## Reproduction

```powershell
python research/crypto_backtest/run_bybit_multisymbol_v1.py --start 2023-01-01 --end 2026-08-29
```

Input checksums and exact data file paths are in `manifest.json`; per-symbol, per-scenario trade logs are adjacent to this report.
