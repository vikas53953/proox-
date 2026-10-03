"""Tick-rule order-flow PROXY (R08).

Real delta/footprint needs licensed trade events with aggressor side. When only trade
prices are available, the tick rule guesses side from price change. That guess is always
labelled PROXY with its limits, and is never presented as executed flow.
"""

from dataclasses import dataclass
from decimal import Decimal

ORDERFLOW_VERSION = "tick-rule-v1"
TICK_RULE_LIMITS = (
    "tick-rule proxy: side inferred from price change, not exchange aggressor flag; "
    "misclassifies trades inside the spread"
)


@dataclass(frozen=True)
class Trade:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class TickRuleDelta:
    delta: Decimal
    classified_volume: Decimal
    unclassified_volume: Decimal
    version: str = ORDERFLOW_VERSION


def tick_rule_delta(trades: list[Trade]) -> TickRuleDelta:
    delta = classified = unclassified = Decimal(0)
    sign = 0
    prev: Decimal | None = None
    for t in trades:
        if prev is not None and t.price != prev:
            sign = 1 if t.price > prev else -1
        prev = t.price
        if sign == 0:
            unclassified += t.quantity
        else:
            classified += t.quantity
            delta += sign * t.quantity
    return TickRuleDelta(delta, classified, unclassified)
