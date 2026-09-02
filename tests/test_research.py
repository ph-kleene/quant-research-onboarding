import numpy as np
import pandas as pd
import pytest

from quant_onboarding.research import (
    adjusted_returns,
    controlled_failure_configs,
    decay_ic,
    forward_return,
    grouped_returns,
    information_coefficient,
    low_volatility,
    momentum_12_1,
    monotonicity_score,
    neutralize,
    performance_summary,
    process_cross_section,
    split_sample,
    standardize,
    top_quantile_weights,
    validate_time_order,
    value_signal,
    winsorize,
)


def test_adjusted_return_matches_hand_calculation_without_final_factor():
    price = pd.Series([10.0, 11.0, 5.8])
    factor = pd.Series([1.0, 1.0, 2.0])
    result = adjusted_returns(price, factor)
    assert np.isnan(result.iloc[0])
    assert result.iloc[1] == pytest.approx(0.1)
    assert result.iloc[2] == pytest.approx((5.8 * 2) / (11 * 1) - 1)


def test_forward_label_is_shifted_not_contemporaneous():
    returns = pd.Series([0.01, 0.02, 0.03])
    assert forward_return(returns).tolist()[:2] == [0.02, 0.03]
    assert np.isnan(forward_return(returns).iloc[-1])


def test_research_clock_requires_usable_signal_execution_order():
    valid = pd.DataFrame(
        {
            "usable_from": ["2025-01-31T17:30:00+08:00"],
            "signal_at": ["2025-01-31T18:00:00+08:00"],
            "execution_at": ["2025-02-03T09:30:00+08:00"],
        }
    )
    assert validate_time_order(valid)
    invalid = valid.copy()
    invalid["execution_at"] = invalid["signal_at"]
    assert not validate_time_order(invalid)
    with pytest.raises(ValueError, match="missing research-clock"):
        validate_time_order(valid.drop(columns="usable_from"))


def test_winsorize_standardize_and_neutralize():
    series = pd.Series([1.0, 2.0, 3.0, 100.0])
    clipped = winsorize(series, 0.0, 0.75)
    assert clipped.max() < 100
    z = standardize(clipped)
    assert z.mean() == pytest.approx(0)
    assert z.std(ddof=0) == pytest.approx(1)
    cap = pd.Series([10.0, 20.0, 40.0, 80.0])
    signal = 2 + 3 * np.log(cap)
    assert neutralize(signal, cap).abs().max() < 1e-10


def test_factor_directions_and_cross_section_processing():
    assert value_signal(pd.Series([1.0, 2.0])).iloc[0] > value_signal(pd.Series([1.0, 2.0])).iloc[1]
    returns = pd.Series(np.linspace(-0.01, 0.02, 300))
    assert momentum_12_1(returns).notna().any()
    assert low_volatility(pd.Series([0.01, -0.01] * 40)).dropna().le(0).all()
    frame = pd.DataFrame(
        {
            "value": [-1.0, -2.0, -3.0, -4.0],
            "momentum": [4.0, 3.0, 2.0, 1.0],
            "low_volatility": [1.0, 2.0, 4.0, 3.0],
            "circ_mv": [10.0, 30.0, 20.0, 50.0],
        }
    )
    result = process_cross_section(frame, ["value", "momentum", "low_volatility"])
    assert {"value_z", "momentum_z", "low_volatility_z", "composite"} <= set(result)


def test_ic_groups_decay_weights_and_performance():
    signal = pd.Series(range(10), dtype=float)
    label = signal * 0.01
    assert information_coefficient(signal, label) == pytest.approx(1.0)
    groups = grouped_returns(signal, label, 5)
    assert len(groups) == 5
    assert monotonicity_score(groups) == pytest.approx(1.0)
    decay = decay_ic(signal, {1: label, 5: -label})
    assert decay.loc[1] == pytest.approx(1.0)
    assert decay.loc[5] == pytest.approx(-1.0)
    weights = top_quantile_weights(signal, 0.2)
    assert weights.sum() == pytest.approx(1.0)
    assert (weights > 0).sum() == 2
    summary = performance_summary(pd.Series([0.001] * 252))
    assert summary.annual_return > 0
    assert summary.max_drawdown == pytest.approx(0)


def test_sample_split_and_four_single_error_experiments():
    frame = pd.DataFrame({"trade_date": ["2022-12-31", "2023-01-01"], "x": [1, 2]})
    split = split_sample(frame)
    assert split["exploration"]["x"].tolist() == [1]
    assert split["confirmation"]["x"].tolist() == [2]
    configs = controlled_failure_configs()
    assert set(configs) == {
        "correct",
        "E1_future_data",
        "E2_survivorship",
        "E3_ignore_costs",
        "E4_confirmation_pollution",
    }
    assert all(
        sum(config.values()) == (0 if name == "correct" else 1) for name, config in configs.items()
    )
