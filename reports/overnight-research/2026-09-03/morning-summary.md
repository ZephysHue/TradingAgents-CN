# 夜间多资产量化研究 v2 晨报

- 启动时间：2026-09-03 08:34:49.602896+08:00
- 完成时间：2026-09-03 08:42:13.106424+08:00
- 状态：completed
- 运行次数：1
- 模式：paper-only / 本地模型 / 固定 Bybit 15m 缓存 / 1000 USDT / 不自动晋级
- 输出目录：`reports\overnight-research\2026-09-03`

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
- /v1/completions: 200

## 各策略族结果

| 家族 | Development 选中参数 | Development 指标 | Validation | Holdout | 成本压力 |
|---|---|---|---|---|---|
| A | `A08` {"bb_width": 2.0, "bb_window": 18, "family": "A", "max_hold_bars": 12, "param_id": "A08", "rsi_entry": 25, "rsi_length": 14} | trades=689, wr=45.28, exp=39.91bp, pf=1.34 | rejected_in_validation / trades=728, exp=-23.60bp, pf=0.71 | not_run | not_run |
| B | `B08` {"family": "B", "max_hold_bars": 16, "param_id": "B08", "pullback_ema": 20, "rsi_floor": 55, "rsi_length": 14, "take_profit_atr": 1.5, "trend_fast": 36, "trend_slow": 120} | trades=1973, wr=41.00, exp=-10.67bp, pf=0.86 | rejected_in_validation / trades=2240, exp=-12.17bp, pf=0.78 | not_run | not_run |
| C | `C03` {"family": "C", "hold_hours": 8, "lookback_hours": 12, "param_id": "C03", "rebalance_hours": 1, "selection_pct": 0.2} | trades=672, wr=45.24, exp=4.16bp, pf=1.01 | rejected_in_validation / trades=700, exp=40.61bp, pf=1.19 | not_run | not_run |
| D | `D12` {"atr_ratio_threshold": 1.2, "breakout_buffer_atr": 0.0, "donchian_window": 40, "family": "D", "max_hold_bars": 20, "param_id": "D12", "take_profit_atr": 2.0} | trades=821, wr=35.08, exp=-13.81bp, pf=0.87 | rejected_in_validation / trades=892, exp=1.58bp, pf=1.01 | not_run | not_run |

## 候选与拒绝

- exploratory challenger 数量：0
- reject 数量：4
- 拒绝 `A` / `A08` @ validation：['validation_win_rate_le_50', 'validation_expectancy_le_0', 'validation_profit_factor_le_1.05']
- 拒绝 `B` / `B08` @ validation：['validation_win_rate_le_50', 'validation_expectancy_le_0', 'validation_profit_factor_le_1.05', 'validation_drawdown_lt_-25']
- 拒绝 `C` / `C03` @ validation：['validation_win_rate_le_50']
- 拒绝 `D` / `D12` @ validation：['validation_win_rate_le_50', 'validation_profit_factor_le_1.05']

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
