"""Generate public deterministic fixture evidence and explanatory artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt

from quant_onboarding.teaching import evaluate_teaching_case, serializable_summary

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    result = evaluate_teaching_case()
    evidence = ROOT / "evidence"
    fixtures = ROOT / "fixtures"
    evidence.mkdir(exist_ok=True)
    fixtures.mkdir(exist_ok=True)
    result["panel"].to_csv(fixtures / "teaching_panel.csv", index=False, float_format="%.10g")
    (fixtures / "README.md").write_text(
        "# 确定性教学夹具\n\n`teaching_panel.csv` 由固定种子生成，只用于测试、无 Token 预览和失败实验，不是市场数据或投资证据。\n",
        encoding="utf-8",
    )
    summary = serializable_summary(result)
    (evidence / "public-case-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    metrics = result["summary"]
    case_md = f"""::: {{.evidence}}\n**教学夹具状态：方法演示，不是市场证据。** 固定种子 `{metrics["seed"]}`；{metrics["months"]} 个教学月份、{metrics["assets"]} 个虚构证券。\n:::\n\n| 证据 | 教学结果 |\n|---|---:|\n| 月均 IC | {metrics["mean_ic"]:.3f} |\n| ICIR | {metrics["icir"]:.3f} |\n| 五组均值是否单调 | {"是" if metrics["group_monotonic"] else "否"} |\n| 教学组合年化收益 | {metrics["annual_return"]:.2%} |\n| 年化波动 | {metrics["annual_volatility"]:.2%} |\n| 最大回撤 | {metrics["max_drawdown"]:.2%} |\n| 月均换手 | {metrics["average_turnover"]:.2%} |\n| 成本假设 | 单边 {metrics["cost_bps"]} bps，仅实际成交计提 |\n| 基准 | {metrics["benchmark"]} |\n\n结果的用途是证明公式、排序、成本和门禁能运行；不能用于评价沪深300。\n"""
    (evidence / "public-case-summary.md").write_text(case_md, encoding="utf-8")
    reg = result["reg"]
    rows = "\n".join(
        f"| {gate['code']} {gate['name']} | {gate['status']} | {gate['evidence']} | {gate['next_step']} |"
        for gate in reg["gates"]
    )
    reg_md = f"""::: {{.evidence}}\n**教学夹具 REG**：{reg["research_validity"]}；{reg["strategy_action"]}。此判断只验证 REG 行为。\n:::\n\n| 门 | 状态 | 证据 | 下一步 |\n|---|---|---|---|\n{rows}\n"""
    (evidence / "public-reg-summary.md").write_text(reg_md, encoding="utf-8")
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams["svg.fonttype"] = "none"  # Use <text> elements, not paths
    plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    figure, axis = plt.subplots(figsize=(11, 6))
    colors = {
        "correct": "#0f766e",
        "E1_future_data": "#b91c1c",
        "E2_survivorship": "#d97706",
        "E3_ignore_costs": "#2563eb",
        "E4_confirmation_reselection": "#7c3aed",
        "benchmark": "#64748b",
    }
    for name, series in result["returns"].items():
        wealth = (1 + series.fillna(0)).cumprod()
        axis.plot(
            wealth.index,
            wealth.values,
            label=name,
            color=colors[name],
            linewidth=2.4 if name == "correct" else 1.5,
        )
    axis.set_title("教学夹具：正确管线与四个受控失败实验（非市场结果）")
    axis.set_xlabel("教学日期（月度）")
    axis.set_ylabel("累计值（对数尺度，起点=1）")
    axis.set_yscale("log")
    axis.legend(ncol=2, frameon=False)
    axis.text(
        0.01,
        -0.16,
        "固定种子生成；单边20bps；基准为不含股息的价格指数夹具；实验ID=TEACHING-V1",
        transform=axis.transAxes,
        fontsize=9,
        color="#475569",
    )
    figure.tight_layout()
    figure.savefig(evidence / "teaching-case.svg", format="svg", metadata={"Date": None})
    plt.close(figure)


if __name__ == "__main__":
    main()
