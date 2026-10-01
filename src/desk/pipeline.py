"""End-to-end report pipeline: calendar -> research -> R01-R15 -> review -> assemble.

On a non-trading day it returns NoReport instead of producing anything — the desk never
re-sends yesterday's report as today's."""

from dataclasses import dataclass
from datetime import date

from desk.agents.model import ModelAdapter
from desk.agents.research import gather
from desk.agents.reviewer import review
from desk.core.lens import LensId, LensResult
from desk.feeds.base import FeedAdapter
from desk.lenses import (
    r01_news,
    r02_global,
    r03_macro,
    r04_sectors,
    r05_largecaps,
    r06_price,
    r07_depth,
    r08_orderflow,
    r09_fno,
    r10_flows,
    r11_sentiment,
    r12_amt,
    r13_options,
    r14_outlook,
    r15_quality,
)
from desk.lenses.context import LensContext, ReportKind
from desk.market_calendar import (
    CALENDAR_LOGIC_VERSION,
    CalendarNotCoveredError,
    TradingCalendar,
    auction_cutoff_utc,
    morning_cutoff_utc,
    previous_trading_day,
)
from desk.quant.levels import LEVELS_VERSION
from desk.quant.options import OPTIONS_VERSION
from desk.quant.orderflow import ORDERFLOW_VERSION
from desk.quant.profile import PROFILE_VERSION
from desk.report.assemble import assemble
from desk.report.model import Report

BEFORE_R14 = (
    r01_news,
    r02_global,
    r03_macro,
    r04_sectors,
    r05_largecaps,
    r06_price,
    r07_depth,
    r08_orderflow,
    r09_fno,
    r10_flows,
    r11_sentiment,
    r12_amt,
    r13_options,
)
METHOD_VERSIONS = {
    "calendar": CALENDAR_LOGIC_VERSION,
    "levels": LEVELS_VERSION,
    "profile": PROFILE_VERSION,
    "options": OPTIONS_VERSION,
    "orderflow": ORDERFLOW_VERSION,
}


@dataclass(frozen=True)
class NoReport:
    trading_date: date
    reason: str


def run_report(
    *,
    calendar: TradingCalendar,
    feed: FeedAdapter,
    model: ModelAdapter,
    trading_date: date,
    kind: ReportKind = ReportKind.MORNING,
    version: int = 1,
) -> Report | NoReport:
    try:
        if not calendar.is_trading_day(trading_date):
            return NoReport(trading_date, f"not a trading day per calendar {calendar.version}")
        if calendar.is_special_session(trading_date):
            # Owner decision pending: no 08:45 report on special sessions (e.g. Muhurat).
            timing = calendar.special_sessions[trading_date]
            return NoReport(
                trading_date, f"special session (timing {timing}); morning report not scheduled"
            )
        prev = previous_trading_day(calendar, trading_date)
    except CalendarNotCoveredError as exc:
        return NoReport(trading_date, f"calendar does not cover this date: {exc}")

    cutoff = (morning_cutoff_utc if kind is ReportKind.MORNING else auction_cutoff_utc)(
        trading_date
    )
    ctx = LensContext(
        trading_date=trading_date,
        prev_trading_date=prev,
        cutoff=cutoff,
        kind=kind,
        capabilities=feed.capabilities(),
        datasets=gather(feed, trading_date),
        model=model,
    )
    for module in BEFORE_R14:
        result: LensResult = module.build(ctx)
        ctx.results[result.lens] = result
    r14, scenarios = r14_outlook.build(ctx)
    ctx.results[LensId.R14] = r14
    ctx.results[LensId.R15] = r15_quality.build(ctx)

    lenses = [ctx.results[lid] for lid in LensId]
    is_mock = feed.is_mock or model.is_mock or calendar.is_mock
    return assemble(
        kind=kind,
        trading_date=trading_date,
        cutoff=cutoff,
        version=version,
        is_mock=is_mock,
        feed=feed.name,
        model=model.name,
        calendar_version=calendar.version,
        method_versions=METHOD_VERSIONS,
        lenses=lenses,
        scenarios=scenarios,
        review=review(lenses, scenarios, cutoff, is_mock),
    )
