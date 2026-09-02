import pandas as pd

from quant_onboarding.research import validate_time_order
from quant_onboarding.teaching import SEED, evaluate_teaching_case, generate_teaching_panel


def test_teaching_fixture_is_deterministic_and_time_ordered():
    first = generate_teaching_panel(months=3, assets=5)
    second = generate_teaching_panel(months=3, assets=5)
    pd.testing.assert_frame_equal(first, second)
    assert validate_time_order(first)
    assert SEED == 20260901


def test_four_failure_experiments_are_distinct_and_explainable():
    result = evaluate_teaching_case()
    returns = result["returns"]
    expected = {
        "correct",
        "E1_future_data",
        "E2_survivorship",
        "E3_ignore_costs",
        "E4_confirmation_reselection",
        "benchmark",
    }
    assert set(returns) == expected
    assert returns["E1_future_data"].mean() > returns["correct"].mean()
    assert returns["E3_ignore_costs"].mean() > returns["correct"].mean()
    assert not returns["E2_survivorship"].equals(returns["correct"])
    assert result["summary"]["chosen_after_confirmation"] in {
        "value_z",
        "momentum_z",
        "low_volatility_z",
        "composite",
    }
    assert result["reg"]["research_validity"].startswith("有效")
