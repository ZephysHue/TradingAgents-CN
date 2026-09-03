# 夜间多资产量化研究 v2 晨报

- 启动时间：2026-09-03 03:32:51.668294+08:00
- 完成时间：2026-09-03 03:36:47.262354+08:00
- 状态：completed
- 运行次数：1
- 模式：paper-only / 本地模型 / 固定 Bybit 15m 缓存 / 1000 USDT / 不自动晋级
- 输出目录：`reports\overnight-research\2026-09-03\debug-small-run2`

## 冻结输入摘要

- Development: 2026-06-01..2026-06-30
- Validation: 2026-07-01..2026-07-31
- Holdout: 2026-08-01..2026-08-29
- 单标的 10% / 组合 80% / 无杠杆 / Funding 标记为未建模不可部署
- 参数预算：A=24, B=32, C=24, D=16, 总计=96 <= 192

## Development 市场结构摘要

```json
{
  "stage": "development",
  "symbols": 30,
  "tiers": {
    "hot": {
      "symbols": 10,
      "median_period_return_pct": -21.949470996018494,
      "median_abs_15m_return_pct": 0.21755427664842952,
      "median_coverage_pct": 100.0
    },
    "mid": {
      "symbols": 10,
      "median_period_return_pct": -24.23164679410343,
      "median_abs_15m_return_pct": 0.3148389498994375,
      "median_coverage_pct": 100.0
    },
    "low": {
      "symbols": 10,
      "median_period_return_pct": -30.66495222403053,
      "median_abs_15m_return_pct": 0.2816311718018494,
      "median_coverage_pct": 100.0
    }
  }
}
```

## 本地模型原始分析状态

- /health: 200 {'status': 'ok'}
- /v1/models: 200
- /v1/chat/completions: ReadTimeout(ReadTimeoutError("HTTPConnectionPool(host='127.0.0.1', port=1234): Read timed out. (read timeout=180)"))

## 各策略族结果

| 家族 | Development 选中参数 | Development 指标 | Validation | Holdout | 成本压力 |
|---|---|---|---|---|---|
| A | `A01` {"bb_width": 1.8, "bb_window": 18, "family": "A", "max_hold_bars": 12, "param_id": "A01", "rsi_entry": 20, "rsi_length": 7} | trades=1616, wr=44.12, exp=13.10bp, pf=1.12 | rejected_in_validation / trades=1778, exp=-16.58bp, pf=0.76 | not_run | not_run |
| B | `B01` {"family": "B", "max_hold_bars": 16, "param_id": "B01", "pullback_ema": 16, "rsi_floor": 50, "rsi_length": 10, "take_profit_atr": 1.5, "trend_fast": 36, "trend_slow": 120} | trades=2830, wr=41.34, exp=-16.11bp, pf=0.80 | rejected_in_validation / trades=3174, exp=-12.19bp, pf=0.78 | not_run | not_run |
| C | `C01` {"family": "C", "hold_hours": 4, "lookback_hours": 12, "param_id": "C01", "rebalance_hours": 1, "selection_pct": 0.2} | trades=1339, wr=46.30, exp=-0.01bp, pf=0.99 | rejected_in_validation / trades=1393, exp=19.80bp, pf=1.13 | not_run | not_run |
| D | `D01` {"atr_ratio_threshold": 1.0, "breakout_buffer_atr": 0.0, "donchian_window": 20, "family": "D", "max_hold_bars": 12, "param_id": "D01", "take_profit_atr": 2.0} | trades=1828, wr=35.56, exp=-22.11bp, pf=0.76 | rejected_in_validation / trades=1989, exp=-7.14bp, pf=0.89 | not_run | not_run |

## 候选与拒绝

- exploratory challenger 数量：0
- reject 数量：4
- 拒绝 `A` / `A01` @ validation：['validation_win_rate_le_50', 'validation_expectancy_le_0', 'validation_profit_factor_le_1.05', 'validation_drawdown_lt_-25']
- 拒绝 `B` / `B01` @ validation：['validation_win_rate_le_50', 'validation_expectancy_le_0', 'validation_profit_factor_le_1.05', 'validation_drawdown_lt_-25']
- 拒绝 `C` / `C01` @ validation：['validation_win_rate_le_50']
- 拒绝 `D` / `D01` @ validation：['validation_win_rate_le_50', 'validation_expectancy_le_0', 'validation_profit_factor_le_1.05', 'validation_drawdown_lt_-25']

## 1000U 权益曲线摘要

- 详见 `equity_curves.csv` 与 `drawdown_summary.csv`；所有阶段独立从 1000 USDT 起算。
- 未在 Holdout 通过的家族不会进入 Paper Registry，也不会自动晋级。

## 风险与限制

- Funding 未建模：`funding_unmodeled_not_deployable`。
- 当前宇宙来自冻结月度名单，仍存在幸存者偏差标记：`exploratory_survivorship_bias_present`。
- 任何 challenger 仅属 exploratory，未接入实盘或自动 champion 流程。

## 10 分钟人工审计清单

1. 打开 `parameter_budget.json`，确认四族参数数分别为 24/32/24/16，总计 96。
2. 抽查 `trade_ledgers/*_development_ledger.csv`，确认 notional 从 1000U 的 10% 起算。
3. 打开 `drawdown_summary.csv`，确认任何候选 Holdout 最大回撤未低于 -25%。
4. 对照 `cost_stress_results.csv`，确认候选在 1.5x 成本下仍为正期望。
5. 查看 `model_analysis_raw.json`，确认模型只做结构分析、没有盈利断言。
6. 查看 `candidate_registry.json` / `rejection_registry.json`，确认没有任何 `paper_champion`。
