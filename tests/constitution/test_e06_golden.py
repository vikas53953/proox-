"""E06 numeric / profile / options golden fixtures and news contradictions (RC05, RC06).

Tolerance: zero. All maths is Decimal and quantised by the method itself, so golden
values must match exactly; any change means a method change and a version bump.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from desk.core.scenario import Scenario
from desk.feeds.base import DatasetKind
from desk.lenses.bars_io import to_bars
from desk.lenses.r01_news import unique_stories
from desk.lenses.r11_sentiment import Contradiction, find_contradictions
from desk.lenses.r13_options import to_chain
from desk.market_calendar import (
    TradingCalendar,
    morning_cutoff_utc,
    previous_trading_day,
)
from desk.quant.bars import Bar, completed_bars
from desk.quant.levels import prior_session_levels
from desk.quant.options import (
    atm_strike,
    basis,
    max_pain,
    oi_change_pct,
    put_call_ratio_oi,
    put_call_ratio_volume,
)
from desk.quant.orderflow import Trade, tick_rule_delta
from desk.quant.profile import value_area
from tests.conftest import MOCK_DAY, feed

D = Decimal
CUTOFF = morning_cutoff_utc(MOCK_DAY)


def flat_bar(price: int, volume: int, minute: int) -> Bar:
    start = datetime(2026, 9, 30, 4, minute, tzinfo=UTC)
    p = D(price)
    return Bar("X FUT", start, start + timedelta(minutes=1), p, p, p, p, D(volume))


# ---- levels (R06) ----------------------------------------------------------------------


def test_prior_session_levels_golden():
    bars = to_bars(feed("full_mock").fetch(DatasetKind.INDEX_BARS, MOCK_DAY))
    lv = prior_session_levels(bars, CUTOFF)
    assert lv.session == date(2026, 9, 30)  # the 29 Sep bar is ignored
    assert (lv.high, lv.low, lv.close, lv.range_points, lv.close_location) == (
        D(25180),
        D(24960),
        D(25120),
        D(220),
        D("0.73"),
    )
    assert lv.bars_used == 6


def test_bar_still_running_at_cutoff_is_excluded():
    running = Bar(
        "X", CUTOFF - timedelta(minutes=5), CUTOFF + timedelta(minutes=5), D(1), D(9), D(1), D(5)
    )
    assert completed_bars([running], CUTOFF) == []


bar_prices = st.integers(min_value=20000, max_value=30000)


@settings(max_examples=60, deadline=None)
@given(st.lists(st.tuples(bar_prices, bar_prices, st.integers(1, 600)), min_size=1, max_size=8))
def test_no_look_ahead_future_bars_never_change_levels(future):
    """Adding any bars that end after the cutoff must not change prior-session levels."""
    bars = to_bars(feed("full_mock").fetch(DatasetKind.INDEX_BARS, MOCK_DAY))
    baseline = prior_session_levels(bars, CUTOFF)
    extra = []
    for a, b, mins in future:
        lo, hi = min(a, b), max(a, b)
        start = CUTOFF - timedelta(minutes=1)
        extra.append(
            Bar("NIFTY", start, CUTOFF + timedelta(minutes=mins), D(lo), D(hi), D(lo), D(hi))
        )
    assert prior_session_levels(bars + extra, CUTOFF) == baseline


# ---- profile (R12) ---------------------------------------------------------------------


def test_value_area_hand_worked_golden():
    # Profile 100:10, 101:20, 102:40, 103:20, 104:10 (total 100, target 70).
    # POC 102. Tie 103 vs 101 -> above (60). Then 101(20) > 104(10) -> 80 >= 70. Stop.
    bars = [
        flat_bar(p, v, i)
        for i, (p, v) in enumerate([(100, 10), (101, 20), (102, 40), (103, 20), (104, 10)])
    ]
    va = value_area(bars, D(1))
    assert (va.poc, va.val, va.vah, va.va_volume, va.total_volume) == (
        D("102.5"),
        D(101),
        D(104),
        D(80),
        D(100),
    )


def test_fixture_value_area_golden():
    bars = to_bars(feed("full_mock").fetch(DatasetKind.FUT_BARS, MOCK_DAY))
    session = [b for b in bars if b.session == date(2026, 9, 30)]
    va = value_area(session, D(20))
    assert (va.poc, va.val, va.vah) == (D(25150), D(25060), D(25200))


def test_value_area_refuses_index_without_volume():
    bar = Bar(
        "NIFTY 50",
        CUTOFF - timedelta(hours=1),
        CUTOFF - timedelta(minutes=1),
        D(1),
        D(2),
        D(1),
        D(2),
        None,
    )
    with pytest.raises(ValueError, match="proxy"):
        value_area([bar], D(1))


@settings(max_examples=80, deadline=None)
@given(st.lists(st.tuples(st.integers(100, 140), st.integers(1, 500)), min_size=1, max_size=25))
def test_value_area_invariants(rows):
    bars = [flat_bar(p, v, i % 60) for i, (p, v) in enumerate(rows)]
    va = value_area(bars, D(1))
    assert va.val <= va.poc <= va.vah
    assert va.va_volume >= va.total_volume * D("0.70")
    assert va.va_volume <= va.total_volume


# ---- options / F&O (R09, R13) ------------------------------------------------------------


def test_option_chain_golden():
    ds = feed("full_mock").fetch(DatasetKind.OPTION_CHAIN, MOCK_DAY)
    chain = to_chain(ds.records)
    assert put_call_ratio_oi(chain) == D("0.92")  # 18.9M / 20.6M
    assert put_call_ratio_volume(chain) == D("0.89")  # 1.53M / 1.72M
    assert max_pain(chain) == D(25100)  # payouts 2780/1420/930/1430/2880
    assert atm_strike(chain, D(25120)) == D(25100)


def test_basis_and_oi_change_golden():
    assert basis(D(25165), D(25120)) == (D(45), D("0.18"))
    assert oi_change_pct(D(15000000), D(14250000)) == D("5.26")
    assert oi_change_pct(D(5), D(0)) is None  # undefined, not zero


def test_pcr_undefined_without_calls_is_none_not_zero():
    from desk.quant.options import ChainRow

    row = ChainRow(D(100), D(0), D(10), D(0), D(10))
    assert put_call_ratio_oi([row]) is None
    assert put_call_ratio_volume([row]) is None


# ---- order-flow proxy (R08) --------------------------------------------------------------


def test_tick_rule_golden():
    ds = feed("full_mock").fetch(DatasetKind.TRADES, MOCK_DAY)
    res = tick_rule_delta([Trade(D(t["price"]), D(t["qty"])) for t in ds.records])
    # first trade unclassified(50); +75 +25 -100 -40(zero tick keeps sign) +60
    assert (res.delta, res.classified_volume, res.unclassified_volume) == (D(20), D(300), D(50))


# ---- news dedupe and contradictions (R01, R11) -----------------------------------------


def test_repost_collapses_to_earliest_original():
    news = feed("full_mock").fetch(DatasetKind.NEWS, MOCK_DAY).records
    stories = unique_stories(news)
    assert len(news) == 5 and len(stories) == 4
    assert next(s for s in stories if s["origin_id"] == "o1")["id"] == "n1"


def test_contradiction_golden():
    news = feed("full_mock").fetch(DatasetKind.NEWS, MOCK_DAY).records
    assert find_contradictions(news) == [Contradiction("MOCKBANK_A", ("o1",), ("o2",))]


def test_same_origin_twice_is_not_a_contradiction():
    item = {
        "origin_id": "o9",
        "published_at": "2026-09-30T10:00:00+05:30",
        "entities": ["Z"],
        "stance": "positive",
    }
    assert find_contradictions([item, item | {"published_at": "2026-09-30T11:00:00+05:30"}]) == []


def test_contradiction_reaches_r14_contrary_evidence(build_report):
    report = build_report()
    assert all(any("MOCKBANK_A" in c for c in s.contrary_evidence) for s in report.scenarios)


# ---- RC06 scenario language ------------------------------------------------------------

BASE = dict(path="UP", condition="c", trigger="t on X", invalidation="i", confidence="low")


@pytest.mark.parametrize(
    "text",
    [
        "Guaranteed breakout",
        "This will surely rally",
        "sure-shot setup",
        "risk-free trade",
        "70% chance of upside",
        "probability of 60%",
        "65 % likely",
    ],
)
def test_scenarios_reject_certainty_and_numeric_probability(text):
    with pytest.raises(ValidationError):
        Scenario(**(BASE | {"trigger": text}))


def test_scenario_allows_levels_and_needs_invalidation():
    Scenario(**(BASE | {"trigger": "Acceptance above VAH 25200 on NIFTY FUT"}))
    with pytest.raises(ValidationError):
        Scenario(**(BASE | {"invalidation": " "}))


def test_report_has_exactly_base_up_down(build_report):
    assert sorted(s.path for s in build_report().scenarios) == ["BASE", "DOWN", "UP"]


# ---- calendar (RC05 versioned calendar) ------------------------------------------------


def test_calendar_rules(mock_calendar: TradingCalendar):
    assert mock_calendar.is_trading_day(date(2026, 10, 1))
    assert not mock_calendar.is_trading_day(date(2026, 10, 2))  # MOCK holiday
    assert not mock_calendar.is_trading_day(date(2026, 10, 3))  # Saturday
    assert mock_calendar.is_trading_day(date(2026, 10, 25))  # MOCK special session
    assert previous_trading_day(mock_calendar, date(2026, 10, 5)) == date(2026, 10, 1)


def test_cutoff_is_0845_ist_stored_as_utc():
    assert morning_cutoff_utc(MOCK_DAY) == datetime(2026, 10, 1, 3, 15, tzinfo=UTC)
