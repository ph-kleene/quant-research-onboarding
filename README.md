# 量化研究入门：完成你的第一项可信多因子研究

> 面向量化策略实习生的中文、可执行、可复现学习产品。通过一次真实的沪深 300 三因子研究，学会判断回测结论是否可信。

[![CI](https://github.com/ph-kleene/quant-research-onboarding/actions/workflows/ci.yml/badge.svg)](https://github.com/ph-kleene/quant-research-onboarding/actions/workflows/ci.yml)
[![Pages](https://github.com/ph-kleene/quant-research-onboarding/actions/workflows/pages.yml/badge.svg)](https://ph-kleene.github.io/quant-research-onboarding/)

**在线学习站：<https://ph-kleene.github.io/quant-research-onboarding/>**

## 项目内容

本仓库把中文学习资料、研究代码、可执行 Notebook 与证据记录放在同一套工作流中。案例围绕沪深 300 成分股的**价值、动量、低波动**三因子展开，包含因子构建、研究评价、成交约束与结论审查。

| 模块 | 已有实现 | 源码入口 |
|---|---|---|
| 因子与研究评价 | 去极值、市值中性化、标准化、Rank IC、分组收益、绩效统计与时序校验 | [research.py](src/quant_onboarding/research.py) |
| 成交与成本 | 停牌、涨跌停与无有效成交的处理，延期订单、换手和成本计算 | [execution.py](src/quant_onboarding/execution.py) |
| 结论审查 | 数据、时序、统计、成本、稳健性、复现六项审查，研究护照与确认集记录 | [reg.py](src/quant_onboarding/reg.py) |
| 数据管线 | Tushare 端点探测、缓存、真实案例数据处理 | [probe.py](src/quant_onboarding/probe.py) · [data.py](src/quant_onboarding/data.py) · [real_case.py](src/quant_onboarding/real_case.py) |

技术栈：Python 3.12、pandas、NumPy、SciPy、Jupyter、Tushare、uv、Quarto。依赖版本见 [pyproject.toml](pyproject.toml) 和 [uv.lock](uv.lock)。

资料入口：[研究 Notebook](notebooks/research-case.ipynb) · [研究模板](templates/) · [教学案例图表](evidence/teaching-case.svg) · [需求与设计文档](docs/) · [英文摘要](content/english-summary.qmd)。

## 学习路径

| 时间 | 入口 | 收获 |
|---|---|---|
| 10 分钟 | [理解因子](https://ph-kleene.github.io/quant-research-onboarding/content/factor-intro.html) | 理解因子、排序、组合和可信判断的基本逻辑 |
| 45 分钟 | [跟做研究](https://ph-kleene.github.io/quant-research-onboarding/content/case-study.html) | 跟随完整的三因子案例，从问题到结论 |
| 2 小时 | [动手实践](https://ph-kleene.github.io/quant-research-onboarding/content/practice.html) | 运行 Notebook、修改参数、完成研究护照 |

**课程页面**：首页 → 学习路径（理解因子 → 跟做研究 → 回测陷阱 → 可信度六问）→ 动手实践 → 参考（知识库 · Notebook · 20 步地图 · 自测）

## 本地启动

从仓库根目录运行以下命令。需要先安装 **Bash、uv 和 Quarto**；`setup` 使用 uv 安装 Python 3.12 和项目依赖，不会安装 uv 或 Quarto。真实数据的 Token 文件检查使用 GNU `stat -c`，对应脚本见 [project.sh](scripts/project.sh)。

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

表中的离线命令以环境和依赖已安装为前提。仓库内的[确定性教学数据](fixtures/README.md)可用于无 Token 的教学演示、测试与失败实验；它由固定种子生成，不是市场数据，也不能作为投资证据。

现有 [CI 配置](.github/workflows/ci.yml)依次运行静态检查、pytest、Notebook 执行、站点构建、链接检查与安全/许可审计；执行状态可从上方 CI 链接查看。测试源码见 [tests/](tests/)。

## 真实数据（可选）

Token 保存在仓库外文件（权限 600）：

```text
~/.config/shangchen-quant-research-onboarding/tushare.env
```

文件内容为 `TUSHARE_TOKEN=你的Token`。按脚本要求将该文件权限设为 `600`，然后从仓库根目录运行：

```bash
chmod 600 ~/.config/shangchen-quant-research-onboarding/tushare.env
bash scripts/project.sh probe
bash scripts/project.sh fetch
```

也可以通过环境变量 `TUSHARE_TOKEN` 提供 Token。调用真实数据接口需要相应的 Tushare 访问权限。原始缓存位于 `~/.cache/shangchen-quant-research-onboarding/`，不会进入仓库。

## 真实案例结论

仓库保存的[端点探测报告](evidence/capability-probe.json)记录九端点探测成功；[真实案例摘要](evidence/real-case-summary.md)记录 2018—2025 年研究样本中的 96 个有效月度截面。基准为 `H00300.CSI`（全收益，含股息），价格代码 `000300.SH`。

现有证据的研究状态为 **credible_stop**（证据不足，停止推进）：正式成交数据不完整，成分股滞后一月敏感性未通过。摘要中的组合收益属于信号诊断，不能视为已通过成交约束验证的策略业绩。

结果入口：[真实案例摘要](evidence/real-case-summary.md) · [机器可读证据](evidence/real-case-summary.json) · [教学夹具摘要](evidence/public-case-summary.md) · [跟做研究](https://ph-kleene.github.io/quant-research-onboarding/content/case-study.html)。教学结果与真实市场结果分别保存。

## 目录

```
.
├── README.md                          # 项目说明
├── REQUIREMENTS.md                    # 原始需求（未修改）
├── AI_PROMPTS.md                      # AI 提问记录（17 条）
├── _quarto.yml                        # Quarto 站点配置
├── pyproject.toml                     # Python 项目与依赖
├── uv.lock                            # 锁定依赖版本
├── references.bib                     # 参考文献
├── .env.example                       # 环境变量模板（不含值）
├── .gitignore                         # 秘密、缓存、产物排除
│
├── content/                           # 学习站页面
│   ├── index.qmd                      #   首页
│   ├── factor-intro.qmd               #   第 1 步：理解因子
│   ├── case-study.qmd                 #   第 2 步：跟做研究
│   ├── failure-lab.qmd                #   第 3 步：回测陷阱
│   ├── credibility.qmd                #   第 4 步：可信度六问
│   ├── practice.qmd                   #   第 5 步：动手实践
│   ├── research-map.qmd               #   20 步研究地图
│   ├── glossary.qmd                   #   知识库与工具箱
│   ├── self-test.qmd                  #   八步自测
│   ├── references.qmd                 #   引用与事实核验
│   ├── english-summary.qmd            #   英文摘要
│   ├── quickstart.qmd                 #   （旧版，保留）
│   ├── three-hours.qmd                #   （旧版，保留）
│   ├── deep-dive.qmd                  #   （旧版，保留）
│   ├── reg.qmd                        #   （旧版，保留）
│   ├── passport.qmd                   #   （旧版，保留）
│   └── templates.qmd                  #   （旧版，保留）
│
├── notebooks/
│   └── research-case.ipynb            # 可执行研究 Notebook
│
├── src/quant_onboarding/
│   ├── __init__.py                    # 包声明
│   ├── research.py                    # 因子、IC、分组、绩效、失败实验
│   ├── execution.py                   # 成交状态机、换手、成本
│   ├── reg.py                         # REG 六门、研究护照、确认账本
│   ├── data.py                        # Token、限速、缓存、清单
│   ├── probe.py                       # 九端点能力探测
│   ├── real_case.py                   # 真实数据管线
│   ├── teaching.py                    # 确定性教学夹具
│   └── cli.py                         # 命令行入口
│
├── tests/
│   ├── test_research.py               # 因子、IC、分组、时序
│   ├── test_execution.py              # 成交状态机、换手、成本
│   ├── test_reg.py                    # REG、护照、账本
│   ├── test_data.py                   # Token、限速、缓存、清单
│   ├── test_probe.py                  # 探测与基准发现
│   ├── test_real_case.py              # 冻结键、时钟、E2/E4
│   └── test_teaching.py               # 教学夹具与失败实验
│
├── templates/                         # 可复用研究模板
│   ├── question-card.md               #   问题卡
│   ├── hypothesis-card.md             #   假设卡
│   ├── experiment-registration.md     #   实验登记
│   ├── result-card.md                 #   结果卡
│   ├── reg-review.md                  #   REG 评审
│   ├── failure-diagnosis.md           #   失败诊断
│   ├── research-passport.md           #   研究护照
│   └── data-dictionary.md             #   数据字典
│
├── scripts/
│   ├── project.sh                     # 统一命令入口
│   ├── generate_public_evidence.py    # 生成教学证据与图表
│   ├── execute_notebook.py            # Notebook E2E 验证
│   ├── check_links.py                 # 内部链接检查
│   └── audit_repository.py            # 安全与许可审计
│
├── evidence/                          # 脱敏证据（不可逆聚合）
│   ├── capability-probe.json          #   九端点探测报告
│   ├── real-case-summary.json         #   真实案例 REG 摘要
│   ├── real-case-summary.md           #   真实案例可读摘要
│   ├── public-case-summary.json       #   教学夹具摘要
│   ├── public-case-summary.md         #   教学夹具可读摘要
│   ├── public-reg-summary.md          #   教学夹具 REG
│   └── teaching-case.svg              #   正确 vs 失败实验图表
│
├── fixtures/                          # 确定性教学数据
│   ├── README.md
│   └── teaching_panel.csv
│
├── docs/                              # 需求与设计文档
│   ├── 00-executive-summary.md
│   ├── 01-product-requirements.md
│   ├── 02-solution-options-and-decision.md
│   ├── 03-learning-and-content-design.md
│   ├── 04-technical-design.md
│   ├── 05-implementation-and-validation-plan.md
│   └── 06-requirements-traceability.md
│
├── assets/
│   ├── styles.css                     # 站点样式
│   └── site-preview.svg               # 预览图
│
└── .github/workflows/
    ├── ci.yml                         # CI：测试 + 构建 + 审计
    └── pages.yml                      # GitHub Pages 部署
```

## 交付物

1. **量化研究入门学习资料**：本仓库（Quarto 网站 + Notebook + 源码 + 模板）
2. **AI 提问记录**：`AI_PROMPTS.md`（17 条，完整提示词原文）

## 免责声明

教学用途，不构成投资建议。历史结果不能保证未来表现。

## 许可证

[MIT License](LICENSE)。Tushare 数据不随本仓库再分发。
