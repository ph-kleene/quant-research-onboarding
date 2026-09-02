import pandas as pd
import pytest

from quant_onboarding.execution import (
    MarketOpen,
    PendingOrder,
    decide_fill,
    delay_summary,
    portfolio_return,
    process_pending_orders,
    transaction_cost,
    turnover,
)


@pytest.mark.parametrize(
    ("side", "market", "filled", "reason"),
    [
        ("buy", MarketOpen("20250102", None, None, has_daily=False), False, "no_valid_daily"),
        (
            "buy",
            MarketOpen("20250102", 10, 100, suspended_full_day=True),
            False,
            "full_day_suspension",
        ),
        ("buy", MarketOpen("20250102", 11, 100, up_limit=11), False, "buy_at_up_limit"),
        ("sell", MarketOpen("20250102", 9, 100, down_limit=9), False, "sell_at_down_limit"),
        ("sell", MarketOpen("20250102", 11, 100, up_limit=11), True, "filled_at_open"),
        ("buy", MarketOpen("20250102", 9, 100, down_limit=9), True, "filled_at_open"),
        ("buy", MarketOpen("20250102", 10, 0), False, "no_opening_volume"),
    ],
)
def test_conservative_fill_state_machine(side, market, filled, reason):
    decision = decide_fill(side, market)
    assert decision.filled is filled
    assert decision.reason == reason


def test_delayed_order_keeps_pending_and_later_fills():
    order = PendingOrder("A", "buy", 0.1, "20250102")
    events, pending = process_pending_orders(
        [order], {"A": MarketOpen("20250103", 11, 100, up_limit=11)}
    )
    assert not events[0].filled
    assert pending[0].delayed_days == 1
    events, pending = process_pending_orders(
        pending, {"A": MarketOpen("20250106", 10.5, 100, up_limit=11)}
    )
    assert events[0].filled and not pending
    assert events[0].delayed_days == 1
    assert delay_summary(events) == {
        "delay_count": 1,
        "delay_days": 1,
        "affected_weight": pytest.approx(0.1),
    }


def test_turnover_cost_and_old_holdings_return_are_aligned():
    old = pd.Series({"A": 0.6, "B": 0.4})
    target = pd.Series({"A": 0.4, "C": 0.6})
    actual_turnover = turnover(old, target)
    assert actual_turnover == pytest.approx(0.6)
    cost = transaction_cost(actual_turnover, 20)
    assert cost == pytest.approx(0.0012)
    result = portfolio_return(old, pd.Series({"A": 0.01, "B": -0.02}), cost)
    assert result == pytest.approx(0.6 * 0.01 + 0.4 * -0.02 - cost)
