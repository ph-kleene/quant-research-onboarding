::: {.evidence}
**教学夹具 REG**：有效：D/T/P 无红灯；继续：进入下一阶段评审（不代表投资建议）。此判断只验证 REG 行为。
:::

| 门 | 状态 | 证据 | 下一步 |
|---|---|---|---|
| D 数据 | green | coverage=1.000 | 补齐数据或停止研究 |
| T 时序 | green | time_order_valid=True | 修正时钟和标签后重跑 |
| S 统计 | green | ic_observations=60 | 补充样本或停止推进 |
| C 成本 | green | cost_model_complete=True; net_effect=0.0059 | 补齐受限成交证据、复核成本假设或停止推进 |
| R 稳健性 | green | robust=True | 补做稳健性或停止推进 |
| P 复现 | green | reproducible=True | 补齐环境/数据/配置证据 |
