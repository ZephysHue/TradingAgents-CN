# BTC 多周期趋势 + EMA + ATR 回踩回测

本目录是独立、可复现的研究工程，不接入实盘下单。

## 数据

默认优先尝试 Binance COIN-M `BTCUSD_PERP`，若指定月份不存在则使用 Binance USDⓈ-M `BTCUSDT`，并在 `manifest.json` 标记实际市场，不能把两者结果混为一谈。数据来自 Binance 官方公开归档；归档工具支持 `cm`、`um`，K 线来源分别对应 `/dapi/v1/klines`、`/fapi/v1/klines`。

```powershell
python research/crypto_backtest/download_binance.py --start 2020-01-01 --end 2026-08-29
python research/crypto_backtest/backtest.py --data-dir research/crypto_backtest/data --output-dir reports/crypto-backtest
```

可通过 `HTTPS_PROXY=http://127.0.0.1:7892` 下载。回测使用 UTC，15 分钟为执行周期；高周期指标只在高周期 K 线完成后向后对齐，Swing 只在右侧两根 K 线完成后确认。
