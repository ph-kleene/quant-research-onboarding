"""Conservative next-open execution and portfolio accounting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class MarketOpen:
    trade_date: str
    open_price: float | None
    volume: float | None
    up_limit: float | None = None
    down_limit: float | None = None
    suspended_full_day: bool = False
    has_daily: bool = True


@dataclass(frozen=True)
class FillDecision:
    filled: bool
    reason: str
    price: float | None


def decide_fill(side: Side, market: MarketOpen) -> FillDecision:
    """Apply the frozen daily-data teaching approximation."""

    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if not market.has_daily or market.open_price is None or not np.isfinite(market.open_price):
        return FillDecision(False, "no_valid_daily", None)
    if market.suspended_full_day:
        return FillDecision(False, "full_day_suspension", None)
    if market.volume is None or not np.isfinite(market.volume) or market.volume <= 0:
        return FillDecision(False, "no_opening_volume", None)
    if (
        side == "buy"
        and market.up_limit is not None
        and np.isclose(market.open_price, market.up_limit)
    ):
        return FillDecision(False, "buy_at_up_limit", None)
    if (
        side == "sell"
        and market.down_limit is not None
        and np.isclose(market.open_price, market.down_limit)
    ):
        return FillDecision(False, "sell_at_down_limit", None)
    return FillDecision(True, "filled_at_open", float(market.open_price))


@dataclass
class PendingOrder:
    symbol: str
    side: Side
    target_weight: float
    submitted_date: str
    delayed_days: int = 0


@dataclass(frozen=True)
class ExecutedOrder:
    symbol: str
    side: Side
    target_weight: float
    submitted_date: str
    execution_date: str | None
    fill_price: float | None
    filled: bool
    reason: str
    delayed_days: int


def process_pending_orders(
    orders: list[PendingOrder], market_by_symbol: dict[str, MarketOpen]
) -> tuple[list[ExecutedOrder], list[PendingOrder]]:
    events: list[ExecutedOrder] = []
    remaining: list[PendingOrder] = []
    for order in orders:
        market = market_by_symbol.get(order.symbol, MarketOpen("", None, None, has_daily=False))
        decision = decide_fill(order.side, market)
        events.append(
            ExecutedOrder(
                order.symbol,
                order.side,
                order.target_weight,
                order.submitted_date,
                market.trade_date if decision.filled else None,
                decision.price,
                decision.filled,
                decision.reason,
                order.delayed_days,
            )
        )
        if not decision.filled:
            remaining.append(
                PendingOrder(
                    order.symbol,
                    order.side,
                    order.target_weight,
                    order.submitted_date,
                    order.delayed_days + 1,
                )
            )
    return events, remaining


def turnover(old_weights: pd.Series, new_weights: pd.Series) -> float:
    old_weights, new_weights = old_weights.align(new_weights, join="outer", fill_value=0.0)
    return float(old_weights.sub(new_weights).abs().sum() * 0.5)


def transaction_cost(actual_turnover: float, one_way_bps: float) -> float:
    if actual_turnover < 0 or one_way_bps < 0:
        raise ValueError("turnover and cost must be non-negative")
    return float(actual_turnover * one_way_bps / 10_000.0)


def portfolio_return(held_weights: pd.Series, asset_returns: pd.Series, cost: float = 0.0) -> float:
    weights, returns = held_weights.align(asset_returns, join="left")
    if weights.isna().any():
        raise ValueError("held weights cannot be missing")
    return float((weights * returns.fillna(0.0)).sum() - cost)


def delay_summary(events: list[ExecutedOrder]) -> dict[str, float | int]:
    delayed = [event for event in events if event.delayed_days > 0]
    affected_weight = sum(abs(event.target_weight) for event in delayed)
    return {
        "delay_count": len(delayed),
        "delay_days": sum(event.delayed_days for event in delayed),
        "affected_weight": float(affected_weight),
    }
