"""Deterministic research primitives shared by the notebook, site and tests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


def adjusted_returns(price: pd.Series, adj_factor: pd.Series) -> pd.Series:
    """Compute adjacent adjusted returns without a future-normalized price level."""

    aligned_price, aligned_factor = price.astype(float).align(
        adj_factor.astype(float), join="inner"
    )
    adjusted_level = aligned_price * aligned_factor
    return adjusted_level.div(adjusted_level.shift(1)).sub(1.0).rename("adjusted_return")


def forward_return(returns: pd.Series, periods: int = 1) -> pd.Series:
    """Move a realized return backward only as an explicit future label."""

    if periods < 1:
        raise ValueError("periods must be positive")
    return returns.shift(-periods).rename("forward_return")


def validate_time_order(
    frame: pd.DataFrame,
    *,
    usable_column: str = "usable_from",
    signal_column: str = "signal_at",
    execution_column: str = "execution_at",
) -> bool:
    """Validate the mandatory usable <= signal < execution research clock."""

    required = {usable_column, signal_column, execution_column}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError("missing research-clock columns: " + ", ".join(missing))
    usable = pd.to_datetime(frame[usable_column], utc=True, errors="coerce")
    signal = pd.to_datetime(frame[signal_column], utc=True, errors="coerce")
    execution = pd.to_datetime(frame[execution_column], utc=True, errors="coerce")
    return bool(
        usable.notna().all()
        and signal.notna().all()
        and execution.notna().all()
        and usable.le(signal).all()
        and signal.lt(execution).all()
    )


def winsorize(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    clean = series.astype(float).replace([np.inf, -np.inf], np.nan)
    if not 0 <= lower < upper <= 1:
        raise ValueError("winsor limits must satisfy 0 <= lower < upper <= 1")
    if clean.notna().sum() < 2:
        return clean
    return clean.clip(clean.quantile(lower), clean.quantile(upper))


def standardize(series: pd.Series) -> pd.Series:
    clean = series.astype(float).replace([np.inf, -np.inf], np.nan)
    std = clean.std(ddof=0)
    if not np.isfinite(std) or std == 0:
        return pd.Series(np.where(clean.notna(), 0.0, np.nan), index=clean.index, name=series.name)
    return ((clean - clean.mean()) / std).rename(series.name)


def neutralize(signal: pd.Series, market_cap: pd.Series) -> pd.Series:
    """Return OLS residuals after an intercept and log-market-cap exposure."""

    signal, market_cap = signal.astype(float).align(market_cap.astype(float), join="inner")
    valid = signal.notna() & market_cap.gt(0) & market_cap.notna()
    result = pd.Series(np.nan, index=signal.index, name=signal.name, dtype=float)
    if valid.sum() < 3:
        return result
    x = np.column_stack([np.ones(valid.sum()), np.log(market_cap[valid].to_numpy())])
    y = signal[valid].to_numpy()
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    result.loc[valid] = y - x @ beta
    return result


def value_signal(pb: pd.Series) -> pd.Series:
    """Low positive PB receives the higher raw value score."""

    pb = pb.astype(float)
    return (-pb.where(pb.gt(0))).rename("value")


def momentum_12_1(returns: pd.Series, lookback: int = 252, skip: int = 21) -> pd.Series:
    if lookback <= skip or skip < 1:
        raise ValueError("lookback must exceed a positive skip")
    window = lookback - skip
    gross = returns.astype(float).add(1.0).shift(skip)
    return (
        gross.rolling(window, min_periods=max(20, int(window * 0.8)))
        .apply(np.prod, raw=True)
        .sub(1.0)
        .rename("momentum")
    )


def low_volatility(returns: pd.Series, window: int = 60) -> pd.Series:
    if window < 2:
        raise ValueError("window must be at least two")
    return (
        returns.astype(float)
        .rolling(window, min_periods=max(20, int(window * 0.8)))
        .std(ddof=0)
        .mul(-np.sqrt(252))
        .rename("low_volatility")
    )


def process_cross_section(
    frame: pd.DataFrame, factors: Iterable[str], market_cap: str = "circ_mv"
) -> pd.DataFrame:
    """Winsorize, size-neutralize and standardize factors within one cross-section."""

    result = frame.copy()
    processed: list[str] = []
    for factor in factors:
        name = f"{factor}_z"
        raw = winsorize(result[factor])
        residual = neutralize(raw, result[market_cap])
        result[name] = standardize(residual)
        processed.append(name)
    result["composite"] = result[processed].mean(axis=1, skipna=False)
    return result


def top_quantile_weights(scores: pd.Series, fraction: float = 0.2) -> pd.Series:
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    valid = scores.dropna().sort_values(ascending=False, kind="mergesort")
    if valid.empty:
        return pd.Series(0.0, index=scores.index, name="weight")
    count = max(1, int(np.ceil(len(valid) * fraction)))
    selected = valid.iloc[:count].index
    weights = pd.Series(0.0, index=scores.index, name="weight")
    weights.loc[selected] = 1.0 / count
    return weights


def information_coefficient(signal: pd.Series, label: pd.Series) -> float:
    aligned = pd.concat([signal, label], axis=1).dropna()
    if len(aligned) < 3 or aligned.iloc[:, 0].nunique() < 2 or aligned.iloc[:, 1].nunique() < 2:
        return float("nan")
    return float(spearmanr(aligned.iloc[:, 0], aligned.iloc[:, 1]).statistic)


def ic_summary(ic: pd.Series) -> dict[str, float]:
    clean = ic.dropna().astype(float)
    mean = float(clean.mean()) if not clean.empty else float("nan")
    std = float(clean.std(ddof=1)) if len(clean) > 1 else float("nan")
    return {
        "ic_mean": mean,
        "ic_std": std,
        "icir": mean / std if std and np.isfinite(std) else float("nan"),
        "positive_rate": float((clean > 0).mean()) if not clean.empty else float("nan"),
    }


def grouped_returns(signal: pd.Series, label: pd.Series, groups: int = 5) -> pd.Series:
    aligned = pd.concat([signal.rename("signal"), label.rename("label")], axis=1).dropna()
    if len(aligned) < groups:
        raise ValueError("not enough observations for requested groups")
    ranks = aligned["signal"].rank(method="first")
    aligned["group"] = pd.qcut(ranks, groups, labels=range(1, groups + 1))
    return aligned.groupby("group", observed=True)["label"].mean().rename("group_return")


def monotonicity_score(group_returns: pd.Series) -> float:
    values = group_returns.dropna().to_numpy(dtype=float)
    if len(values) < 2:
        return float("nan")
    return float(spearmanr(np.arange(len(values)), values).statistic)


def decay_ic(signal: pd.Series, future_returns: Mapping[int, pd.Series]) -> pd.Series:
    return pd.Series(
        {
            int(horizon): information_coefficient(signal, label)
            for horizon, label in future_returns.items()
        },
        name="ic",
    )


@dataclass(frozen=True)
class PerformanceSummary:
    annual_return: float
    annual_volatility: float
    sharpe: float
    max_drawdown: float
    cumulative_return: float


def performance_summary(returns: pd.Series, periods_per_year: int = 252) -> PerformanceSummary:
    clean = returns.fillna(0.0).astype(float)
    if clean.empty:
        return PerformanceSummary(*(float("nan"),) * 5)
    wealth = clean.add(1.0).cumprod()
    years = len(clean) / periods_per_year
    annual_return = (
        float(wealth.iloc[-1] ** (1 / years) - 1)
        if years > 0 and wealth.iloc[-1] > 0
        else float("nan")
    )
    annual_vol = float(clean.std(ddof=1) * np.sqrt(periods_per_year))
    sharpe = annual_return / annual_vol if annual_vol > 0 else float("nan")
    drawdown = wealth.div(wealth.cummax()).sub(1.0)
    return PerformanceSummary(
        annual_return, annual_vol, sharpe, float(drawdown.min()), float(wealth.iloc[-1] - 1.0)
    )


def split_sample(frame: pd.DataFrame, date_column: str = "trade_date") -> dict[str, pd.DataFrame]:
    dates = pd.to_datetime(frame[date_column])
    return {
        "exploration": frame.loc[dates.between("2018-01-01", "2022-12-31")].copy(),
        "confirmation": frame.loc[dates.between("2023-01-01", "2025-12-31")].copy(),
    }


def controlled_failure_configs() -> dict[str, dict[str, bool]]:
    return {
        "correct": {
            "future_data": False,
            "current_constituents": False,
            "ignore_costs": False,
            "confirmation_reselection": False,
        },
        "E1_future_data": {
            "future_data": True,
            "current_constituents": False,
            "ignore_costs": False,
            "confirmation_reselection": False,
        },
        "E2_survivorship": {
            "future_data": False,
            "current_constituents": True,
            "ignore_costs": False,
            "confirmation_reselection": False,
        },
        "E3_ignore_costs": {
            "future_data": False,
            "current_constituents": False,
            "ignore_costs": True,
            "confirmation_reselection": False,
        },
        "E4_confirmation_pollution": {
            "future_data": False,
            "current_constituents": False,
            "ignore_costs": False,
            "confirmation_reselection": True,
        },
    }
