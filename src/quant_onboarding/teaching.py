"""Deterministic public fixture; never used as formal market evidence."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .execution import transaction_cost, turnover
from .reg import evaluate_reg
from .research import (
    grouped_returns,
    information_coefficient,
    performance_summary,
    process_cross_section,
    top_quantile_weights,
    validate_time_order,
)

SEED = 20260901


def generate_teaching_panel(months: int = 60, assets: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    dates = pd.date_range("2018-01-31", periods=months, freq="ME")
    symbols = [f"FIX{number:03d}" for number in range(assets)]
    rows: list[dict[str, float | str]] = []
    persistent = rng.normal(size=(assets, 3))
    for date in dates:
        execution_date = date + pd.offsets.BDay(1)
        persistent = 0.82 * persistent + rng.normal(scale=0.58, size=(assets, 3))
        log_cap = rng.normal(10, 0.9, size=assets)
        value = persistent[:, 0] + 0.18 * log_cap
        momentum = persistent[:, 1] - 0.10 * log_cap
        low_volatility = persistent[:, 2] + 0.06 * log_cap
        latent = (persistent[:, 0] + persistent[:, 1] + persistent[:, 2]) / 3
        future = 0.008 * latent + rng.normal(scale=0.035, size=assets)
        benchmark = float(rng.normal(0.004, 0.025))
        for index, symbol in enumerate(symbols):
            rows.append(
                {
                    "trade_date": date.date().isoformat(),
                    "symbol": symbol,
                    "value": float(value[index]),
                    "momentum": float(momentum[index]),
                    "low_volatility": float(low_volatility[index]),
                    "circ_mv": float(np.exp(log_cap[index])),
                    "forward_return": float(future[index]),
                    "benchmark_return": benchmark,
                    "usable_from": f"{date.date().isoformat()}T17:30:00+08:00",
                    "signal_at": f"{date.date().isoformat()}T18:00:00+08:00",
                    "execution_at": f"{execution_date.date().isoformat()}T09:30:00+08:00",
                }
            )
    return pd.DataFrame(rows)


def _monthly_strategy(
    panel: pd.DataFrame,
    score_column: str,
    *,
    cost_bps: float,
    survivor_symbols: set[str] | None = None,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    returns: dict[pd.Timestamp, float] = {}
    gross_returns: dict[pd.Timestamp, float] = {}
    turnovers: dict[pd.Timestamp, float] = {}
    previous = pd.Series(dtype=float)
    for date, cross_section in panel.groupby("trade_date", sort=True):
        if survivor_symbols is not None:
            cross_section = cross_section[cross_section["symbol"].isin(survivor_symbols)]
        weights = top_quantile_weights(cross_section.set_index("symbol")[score_column], 0.2)
        label = cross_section.set_index("symbol")["forward_return"]
        gross = float((weights * label).sum())
        actual_turnover = turnover(previous, weights)
        net = gross - transaction_cost(actual_turnover, cost_bps)
        timestamp = pd.Timestamp(date)
        returns[timestamp] = net
        gross_returns[timestamp] = gross
        turnovers[timestamp] = actual_turnover
        previous = weights
    return pd.Series(returns), pd.Series(gross_returns), pd.Series(turnovers)


def evaluate_teaching_case() -> dict:
    panel = generate_teaching_panel()
    processed = []
    monthly_ic = {}
    group_rows = []
    for date, frame in panel.groupby("trade_date", sort=True):
        result = process_cross_section(
            frame.set_index("symbol"), ["value", "momentum", "low_volatility"]
        )
        result["trade_date"] = date
        result["symbol"] = result.index
        processed.append(result.reset_index(drop=True))
        monthly_ic[date] = information_coefficient(result["composite"], result["forward_return"])
        groups = grouped_returns(result["composite"], result["forward_return"], 5)
        for group, value in groups.items():
            group_rows.append({"trade_date": date, "group": int(group), "return": float(value)})
    scored = pd.concat(processed, ignore_index=True)
    correct, gross, turnovers = _monthly_strategy(scored, "composite", cost_bps=20)
    future = scored.copy()
    future["future_score"] = future["forward_return"]
    leaked, _, _ = _monthly_strategy(future, "future_score", cost_bps=20)
    means = scored.groupby("symbol")["forward_return"].mean().sort_values(ascending=False)
    survivors = set(means.iloc[: int(len(means) * 0.75)].index)
    survivor, _, _ = _monthly_strategy(scored, "composite", cost_bps=20, survivor_symbols=survivors)
    candidates = {}
    for factor in ("value_z", "momentum_z", "low_volatility_z", "composite"):
        candidate, _, _ = _monthly_strategy(scored, factor, cost_bps=20)
        candidates[factor] = candidate
    chosen_name = max(
        candidates, key=lambda name: performance_summary(candidates[name], 12).annual_return
    )
    contaminated = candidates[chosen_name]
    benchmark = scored.groupby("trade_date")["benchmark_return"].first()
    benchmark.index = pd.to_datetime(benchmark.index)
    ic = pd.Series(monthly_ic, dtype=float)
    ic.index = pd.to_datetime(ic.index)
    group_means = pd.DataFrame(group_rows).groupby("group")["return"].mean()
    correct_perf = performance_summary(correct, 12)
    exploration = correct.loc[correct.index <= "2022-12-31"]
    confirmation = correct.loc[correct.index >= "2023-01-01"]
    evidence = {
        "data_coverage": 1.0,
        "time_order_valid": validate_time_order(panel),
        "future_leak": False,
        "reproducible": True,
        "ic_observations": int(ic.notna().sum()),
        "net_effect": float(correct.mean()),
        "robust_across_subperiods": bool(
            exploration.mean() > 0 and (confirmation.empty or confirmation.mean() > 0)
        ),
        "lag_sensitivity_ok": True,
    }
    reg = evaluate_reg(evidence)
    return {
        "panel": panel,
        "scored": scored,
        "returns": {
            "correct": correct,
            "E1_future_data": leaked,
            "E2_survivorship": survivor,
            "E3_ignore_costs": gross,
            "E4_confirmation_reselection": contaminated,
            "benchmark": benchmark,
        },
        "turnover": turnovers,
        "ic": ic,
        "group_returns": group_means,
        "summary": {
            "fixture": True,
            "seed": SEED,
            "months": int(panel["trade_date"].nunique()),
            "assets": int(panel["symbol"].nunique()),
            "mean_ic": float(ic.mean()),
            "icir": float(ic.mean() / ic.std(ddof=1)),
            "group_monotonic": bool(group_means.is_monotonic_increasing),
            "annual_return": correct_perf.annual_return,
            "annual_volatility": correct_perf.annual_volatility,
            "max_drawdown": correct_perf.max_drawdown,
            "average_turnover": float(turnovers.mean()),
            "cost_bps": 20,
            "benchmark": "确定性价格指数夹具（不含股息）",
            "chosen_after_confirmation": chosen_name,
        },
        "reg": reg.to_dict(),
    }


def serializable_summary(result: dict) -> dict:
    return {
        "summary": result["summary"],
        "reg": result["reg"],
        "series": {
            name: {date.date().isoformat(): float(value) for date, value in series.items()}
            for name, series in result["returns"].items()
        },
    }
