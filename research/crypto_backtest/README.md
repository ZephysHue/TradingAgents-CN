# BTC 多周期趋势 + EMA + ATR 回踩回测

本目录是独立、可复现的研究工程，不接入实盘下单。

## 两条研究路线

- `overnight_multi_asset_research_v2.py`：冻结 Bybit USDT 线性永续 15m
  缓存上的多资产组合研究。它管理月度 hot/mid/low 宇宙、1,000 USDT
  独立阶段权益、组合敞口、四策略族以及 Development/Validation/Holdout
  Gate。它不向交易所下单，也不会自动晋级 Paper Champion。
- `M:\vectorbt\apps\backtest-studio\llm_research\research_loop.py`：单一
  DataFrame 的 LLM 假设生成和 VectorBT 回测研究。它不管理多资产组合、
  横截面动量或本目录的 Registry。

两条路线并行，不能把参数、排名或结果混为同一证据链。跨路线复用策略前，
必须在目标执行器的冻结数据和 Gate 下重新验证。

## 数据

默认优先尝试 Binance COIN-M `BTCUSD_PERP`，若指定月份不存在则使用 Binance USDⓈ-M `BTCUSDT`，并在 `manifest.json` 标记实际市场，不能把两者结果混为一谈。数据来自 Binance 官方公开归档；归档工具支持 `cm`、`um`，K 线来源分别对应 `/dapi/v1/klines`、`/fapi/v1/klines`。

```powershell
python research/crypto_backtest/download_binance.py --start 2020-01-01 --end 2026-08-29
python research/crypto_backtest/backtest.py --data-dir research/crypto_backtest/data --output-dir reports/crypto-backtest
```

可通过 `HTTPS_PROXY=http://127.0.0.1:7892` 下载。回测使用 UTC，15 分钟为执行周期；高周期指标只在高周期 K 线完成后向后对齐，Swing 只在右侧两根 K 线完成后确认。
