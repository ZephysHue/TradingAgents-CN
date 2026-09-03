# 夜间多资产量化研究 v2 总报告

## 结论

本轮没有产生可进入 Paper Registry 的策略候选。四个策略族均在 Validation 阶段被拒绝，Holdout 与成本压力测试均未执行。这是正确的 Gate 行为，不是缺失结果。

本轮结果只适用于研究：`exploratory_survivorship_bias_present`、`funding_unmodeled_not_deployable`。

## 可复现运行

- 最终受控运行：2026-09-03 08:34:49 至 08:42:13（UTC+08:00），耗时约 7 分 24 秒。
- 测试：`9 passed in 12.12s`，退出码 0。
- 真实运行：退出码 0。
- 数据清单 SHA256：`088f9c7f69844ac87af493bdf2d54f8deabe157902e3d888732a9fe53444d9a6`。
- 宇宙清单 SHA256：`8f1c6957163a56032f8eff1fdbb9ac4ecaaedc90d9873784471d8c7250617f30`。
- 原始命令、退出码、回滚命令和脚本快照：[`VERIFICATION.txt`](VERIFICATION.txt)。

> 说明：上列时间只代表最终单次受控复跑，不代表整夜任务的总运行时长。此前自动化错误地创建了重复任务线程；重复线程已归档，不能作为本报告的结果来源。

## 冻结实验契约

| 项目 | 设置 |
|---|---|
| 数据 | Bybit USDT 线性永续公开、已冻结的 15m K 线缓存 |
| Development | 2026-06-01 至 2026-06-30 |
| Validation | 2026-07-01 至 2026-07-31 |
| Holdout | 2026-08-01 至 2026-08-29 |
| 宇宙 | 每阶段 hot/mid/low 各 10 个标的，共 30 个 |
| 初始权益 | 每个策略候选、每个阶段独立 1,000 USDT |
| 风险 | 单标的最大名义敞口 10%，组合最大名义敞口 80%，无杠杆 |
| 成交 | 已完成 bar t 的 Close 信号，bar t+1 Open 成交 |
| 基线成本 | 单边手续费 0.04% + 单边滑点 2bp |
| 压力成本 | 基线费用和滑点各乘 1.5 |
| 参数预算 | A=24，B=32，C=24，D=16；总计 96，未超过 192 |

Development 只用于选参；参数锁定后进入 Validation；只有通过 Validation 的候选才可运行一次 Holdout。

## 本地模型接入

- llama-server 健康检查、模型列表和一次 completion 均返回 HTTP 200。
- 实际模型：`DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf`。
- 模型仅分析 Development 市场结构与已选参数；回测、资金、成本、Gate 和拒绝结论由确定性执行器生成。
- 该次 completion 在 192 token 上限处截断，输出不是完整 JSON；因此它只保留为原始审计材料，**不参与任何交易或晋级决策**。

完整请求、模型响应和计时见 [`model_analysis_raw.json`](model_analysis_raw.json)。

## 四个策略族

| 家族 | 策略 | Development 选中参数 | Development | Validation | 结论 |
|---|---|---|---|---|---|
| A | Bollinger + RSI 均值回归 | A08：BB18 / 2.0，RSI14 < 25，最多 12 bar | 689 笔；胜率 45.28%；期望 +39.91bp；PF 1.34；DD -6.29% | 728 笔；胜率 42.99%；期望 -23.60bp；PF 0.71；DD -19.90% | Validation 拒绝 |
| B | 1h 趋势过滤 + 15m 回撤 | B08：EMA36/120，EMA20 回撤，RSI14 >= 55 | 1,973 笔；胜率 41.00%；期望 -10.67bp；PF 0.86；DD -27.38% | 2,240 笔；胜率 38.93%；期望 -12.17bp；PF 0.78；DD -27.13% | Validation 拒绝 |
| C | 分层横截面动量 | C03：12h 回看，20% 多空，8h 持有 | 672 笔；胜率 45.24%；期望 +4.16bp；PF 1.01；DD -17.83% | 700 笔；胜率 44.14%；期望 +40.61bp；PF 1.19；DD -15.69% | Validation 拒绝：胜率未过线 |
| D | Donchian / ATR 突破 | D12：40 bar，ATR 比率 1.2，2 ATR 止盈，最多 20 bar | 821 笔；胜率 35.08%；期望 -13.81bp；PF 0.87；DD -14.34% | 892 笔；胜率 33.41%；期望 +1.58bp；PF 1.01；DD -12.74% | Validation 拒绝 |

## Gate 与资金结论

- Challenger：0。
- Validation 拒绝：4。
- Holdout：0 次运行；原因是四族均未通过 Validation，不允许为了寻找正结果而提前使用 Holdout。
- 成本压力：0 次运行；原因同上。
- Paper Champion：0；没有生成 Paper Registry、纸面订单或实盘订单。

主要拒绝原因：

1. A：Validation 胜率、期望、PF 均未达线。
2. B：Validation 胜率、期望、PF 未达线，且最大回撤低于 -25%。
3. C：Validation 的期望、PF、回撤均可接受，但胜率 44.14% 未达到预注册的 >50% 门槛。
4. D：Validation 胜率和 PF 未达线；+1.58bp 的微弱期望不足以抵消执行不确定性。

## 风险与数据限制

1. Funding 未建模；不能将结果用于部署或交易。
2. 月度宇宙基于冻结的当前上架名单，仍有幸存者偏差。
3. 上述成本是假设成本，不是逐标的、逐时段订单簿冲击成本。
4. 本地模型响应被截断；它没有影响确定性结果，但下一轮应提高输出上限或改为结构化分段响应。
5. 本轮只覆盖一个三个月切分，尚不足以确认跨 regime 稳健性。

## 自动化事故与处置

- 原因：错误创建了短间隔与每小时两个 cron，平台将每次触发渲染为独立任务线程。
- 处置：两个自动化均暂停；已归档 318 个重复的“夜间量化研究”线程；当前主任务未归档。
- 影响：只认可本报告列出的最终单次受控复跑。重复线程不构成额外样本、额外回测或独立证据。

## 下一步

1. 不把 A-D 中任一策略接入 Paper Registry。
2. 先审计 C03 的低胜率、高期望来源：按 tier、方向、月份、持有期和极端交易拆分，而不是直接调参。
3. 为下一轮加入 Funding 历史、逐标的费率/最小名义额和更长的多 regime 时间切分。
4. 修复自动化为单一任务、单一运行编号、单一输出目录后，再安排下一次研究。

## 工件索引

- [晨报](morning-summary.md)
- [Development 结果](development_results.csv)
- [Validation 结果](validation_results.csv)
- [Holdout 结果](holdout_results.csv)
- [拒绝注册表](rejection_registry.json)
- [候选注册表](candidate_registry.json)
- [权益曲线](equity_curves.csv)
- [回撤摘要](drawdown_summary.csv)
- [成本压力](cost_stress_results.csv)
- [验证记录](VERIFICATION.txt)
- [回滚脚本](ROLLBACK.sh)
