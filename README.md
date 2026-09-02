# 量化研究入门：完成你的第一项可信多因子研究

> 面向量化策略实习生的中文、可执行、可复现学习产品。通过一次真实的沪深 300 三因子研究，学会判断回测结论是否可信。

[![CI](https://github.com/ph-kleene/quant-research-onboarding/actions/workflows/ci.yml/badge.svg)](https://github.com/ph-kleene/quant-research-onboarding/actions/workflows/ci.yml)
[![Pages](https://github.com/ph-kleene/quant-research-onboarding/actions/workflows/pages.yml/badge.svg)](https://ph-kleene.github.io/quant-research-onboarding/)

**在线学习站：<https://ph-kleene.github.io/quant-research-onboarding/>**

## 学习路径

| 时间 | 入口 | 收获 |
|---|---|---|
| 10 分钟 | [理解因子](https://ph-kleene.github.io/quant-research-onboarding/content/factor-intro.html) | 理解因子、排序、组合和可信判断的基本逻辑 |
| 45 分钟 | [跟做研究](https://ph-kleene.github.io/quant-research-onboarding/content/case-study.html) | 跟随完整的三因子案例，从问题到结论 |
| 2 小时 | [动手实践](https://ph-kleene.github.io/quant-research-onboarding/content/practice.html) | 运行 Notebook、修改参数、完成研究护照 |

**课程页面**：首页 → 学习路径（理解因子 → 跟做研究 → 回测陷阱 → 可信度六问）→ 动手实践 → 参考（知识库 · Notebook · 20 步地图 · 自测）

## 本地启动

```bash
bash scripts/project.sh setup   # 安装环境（仅第一次）
bash scripts/project.sh test    # 运行测试和 Notebook
bash scripts/project.sh build   # 构建站点
bash scripts/project.sh preview # 本地预览
```

| 命令 | 网络/Token | 作用 |
|---|---|---|
| `setup` | 需要网络 | 安装 uv 管理的 Python 3.12 环境 |
| `test` | 不需要 | ruff 检查 + pytest + Notebook E2E |
| `build` | 不需要 | 生成证据 + Quarto 构建 + 链接检查 |
| `preview` | 不需要 | 本地预览站点 |
| `probe` | 需要 Token | 探测九个 Tushare 端点 |
| `fetch` | 需要 Token | 获取真实数据（缓存写仓库外） |
| `audit` | 不需要 | 安全与许可扫描 |

## 真实数据（可选）

Token 保存在仓库外文件（权限 600）：

```text
~/.config/shangchen-quant-research-onboarding/tushare.env
```

文件内容为 `TUSHARE_TOKEN=你的Token`，然后运行 `probe` 和 `fetch`。原始缓存位于 `~/.cache/shangchen-quant-research-onboarding/`，不会进入仓库。

## 真实案例结论

九端点探测成功。基准为 `H00300.CSI`（全收益，含股息），价格代码 `000300.SH`。2018—2025 共 96 个有效月度截面。成交数据不完整，成分股滞后一月敏感性未通过 → **credible_stop**（证据不足，停止推进）。详见[跟做研究](https://ph-kleene.github.io/quant-research-onboarding/content/case-study.html)。

## 目录

```text
content/        学习站页面（Quarto qmd）
notebooks/      可执行研究 Notebook
src/            研究逻辑、数据管线、执行模型、REG
tests/          44 项单元/契约/回归测试
fixtures/       确定性教学数据
templates/      问题卡、假设卡、结果卡、护照等模板
evidence/       脱敏探测报告、REG 摘要、教学图表
docs/           需求与设计文档（冻结）
scripts/        统一命令入口与验证脚本
```

## 交付物

1. **量化研究入门学习资料**：本仓库（Quarto 网站 + Notebook + 源码 + 模板）
2. **AI 提问记录**：`AI_PROMPTS.md`（17 条，完整提示词原文）

## 免责声明

教学用途，不构成投资建议。历史结果不能保证未来表现。

## 许可证

[MIT License](LICENSE)。Tushare 数据不随本仓库再分发。
