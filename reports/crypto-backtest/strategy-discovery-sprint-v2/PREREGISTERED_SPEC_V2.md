---
id: KB-QUANT-SPRINT-V2-SPEC-001
type: spec
project: 量化研究平台
status: frozen
frozen_at_utc: 2026-09-03
frozen_by: Comate/GLM（审计方）；执行器实现归 GPT
supersedes: v1 胜率门槛（execution-v1，2026-09-03）
related:
  - "[[项目/量化研究平台/协作变更日志]]"
  - "[[项目/量化研究平台/多资产持续研究面板设计]]"
review_after: 2026-09-17
---

# 策略发现冲刺 v2 预注册规格（冻结）

## 0. 冻结声明

本文件是 v2 冲刺的唯一门槛来源。运行任何 v2 回测之前，本文件 SHA256 必须已写入协作日志。
冻结后如需修改：新建版本文件（V2.1/V3…）并记录新 SHA256，**禁止原地覆盖或追加修订段落**。
任何"看到结果之后再调门槛"的行为视为本轮结果作废。

## 1. 背景与修订理由

v1（execution-v1）S1/S2/S3 全部 rejected_in_development，拒绝理由均为未满足 `win_rate > 50`。
该门槛经济依据不足：低胜率 × 高盈亏比的策略同样可以具有正期望；胜率单独不构成经济标准。
故 v2 废除胜率门槛，改为期望/PF/回撤/样本量四要素。胜率保留为描述字段在报告中展示，不参与判定。

v1 中 S1 的 low 层表现（51.6% 胜率、+26.7bp）是**待验证线索**，不是已确认的 alpha。
v2 通过流动性分档成本（第 3 节）直接检验其对成交成本的敏感性。

## 2. 全家族统一门槛（冻结数值）

判定对象：每策略每阶段的 `total` 聚合行。

| # | 要件 | 冻结值 |
| --- | --- | --- |
| 1 | 净期望 `net_expectancy_bps` | > 0 |
| 2 | 盈利因子 `profit_factor` | ≥ 1.05 |
| 3 | 最大回撤 `max_drawdown_pct` | ≥ -25.0 |
| 4 | 最低交易数 `trade_count` | development ≥ 150；validation ≥ 100；holdout ≥ 300（沿用 v1） |

规则：

1. 跨策略族统一适用，**禁止按族、按 tier、按策略微调**。
2. 任何阶段任一要件不满足 → 该阶段 `rejected`，后续阶段一律 `not_run_due_to_previous_gate`。
3. 本规格不保证 S1、S2、S3 或任何策略通过；S1 在分档成本下仍失败是可接受且预期内的可能结果。
4. 成本压力（1.5x）仍只在通过全部阶段后运行（沿用 v1 行为）。

## 3. 流动性分档成本（冻结数值，假设待证据）

基线沿用 v1：taker 手续费 4bp/单边 + 滑点 2bp/单边。分层附加成本（每单边）：

| tier | 附加成本/单边 | 总成本/单边（费+滑+附加） |
| --- | --- | --- |
| hot | +0bp | 6bp |
| mid | +2bp | 8bp |
| low | +10bp | 16bp |

规则：

1. 全部标注 `assumption_pending_spread_evidence`；取得 Bybit 盘口价差实测证据后升级 v2.1 替换数值。
2. **生效范围**：development 的参数选择与排名、validation、holdout 必须全部使用分档后成本——禁止只在最终 Gate 加压（防止选择阶段的脚步 Yet 仍偏向低流动性标的）。
3. 1.5x 成本压力乘数作用于分档后总成本。
4. tier 归属以冻结的 `universe_manifest.json` 为准，运行中不得重排或重分类。

## 4. 数据完整性断言（新增，硬性）

**前置缺陷披露**：v1 `execution-v1/trade_ledger_s1.csv` 存在 96% 重复行（71 个唯一 trade_id / 1,701 行），与 `development_results.csv` 的 1,823 笔不一致，且按 tier 重算的 hot 层期望符号（-15.1bp vs +1.7bp）相互矛盾。该缺陷修复前**不得启动 v2 运行**。

v2 执行器每轮必须断言以下三条，任一失败则整轮 FAIL 并输出 `ledger_integrity_failed` 工件：

1. ledger 中 `trade_id` 唯一（每笔交易恰好一行）；
2. `unique(trade_id) 计数 == development_results.trade_count`（每策略每阶段逐行校验）；
3. `development_results.csv` 的 `win_rate / net_expectancy_bps / profit_factor` 必须可由 ledger 聚合复现（bps 容差 0.1）。

## 5. 边界（不变）

- exploratory-only：结果始终携带 `exploratory_survivorship_bias_present`；
- Funding 未建模：`funding_not_modeled_not_deployable`，不得接入模拟盘、实盘、前端或产品 API；
- LLM 仅限研究环（假设生成与结果解释），不进执行路径；
- 输出永远只有 `rejected | challenger | evidence_not_ready`，不写 `paper_champion`。

## 6. 变更历史

| 版本 | 时间 (UTC) | 内容 | 签发 |
| --- | --- | --- | --- |
| v2.0 (本文件) | 2026-09-03 | 冻结四要素门槛；废除胜率门槛；新增流动性分档成本与 ledger 一致性断言 | Comate/GLM 审计方 |
