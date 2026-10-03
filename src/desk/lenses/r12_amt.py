"""R12 Auction market theory: prior-session value area / POC from a traded PROXY
(index futures), with the fixed method named. Developing profile, initial balance and
opening location need intraday data and are not available pre-market."""

from decimal import Decimal

from desk.core.facts import DataClass, Gap
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.bars_io import to_bars
from desk.lenses.context import LensContext, missing, mk_fact, session_fact_class, stale_gap
from desk.quant.bars import latest_completed_session
from desk.quant.profile import PROFILE_VERSION, value_area

INTRADAY_NA = Gap(
    topic="opening location / initial balance / developing profile",
    data_class=DataClass.NOT_APPLICABLE,
    reason="need intraday trading after 09:15 IST",
)


def build(ctx: LensContext) -> LensResult:
    gaps: list[Gap] = [INTRADAY_NA]
    if not ctx.capabilities.get(DatasetKind.GIFT_NIFTY).granted:
        gaps.append(missing(DatasetKind.GIFT_NIFTY, ctx, "overnight inventory not assessed"))
    fut = ctx.ds(DatasetKind.FUT_BARS)
    if fut is None:
        return LensResult(
            lens=LensId.R12, gaps=(missing(DatasetKind.FUT_BARS, ctx, "no value area"), *gaps)
        )
    cls, why = session_fact_class(ctx, DatasetKind.FUT_BARS, fut)
    session = latest_completed_session(to_bars(fut), ctx.cutoff)
    tick = Decimal(str(fut.meta["profile_tick"]))
    va = value_area(session, tick) if session else None
    if va is None:
        return LensResult(
            lens=LensId.R12, gaps=(missing(DatasetKind.FUT_BARS, ctx, "no completed bars"), *gaps)
        )
    inst = fut.meta["instrument"]
    note = why or (
        f"proxy instrument {inst} for NIFTY 50 cash index; method "
        f"{PROFILE_VERSION}; bin {tick} points; session = prior day's bars"
    )
    pcls = DataClass.STALE if why else DataClass.PROXY
    facts = [
        mk_fact(fut, "Point of control (POC)", va.poc, "points", inst, pcls, note=note),
        mk_fact(fut, "Value area high (VAH)", va.vah, "points", inst, pcls, note=note),
        mk_fact(fut, "Value area low (VAL)", va.val, "points", inst, pcls, note=note),
    ]
    last_close = session[-1].close
    where = (
        "inside"
        if va.val <= last_close <= va.vah
        else ("above" if last_close > va.vah else "below")
    )
    facts.append(mk_fact(fut, "Prior close vs value area", where, "text", inst, pcls, note=note))
    if why:
        gaps.append(stale_gap(DatasetKind.FUT_BARS, why))
    return LensResult(
        lens=LensId.R12,
        facts=tuple(facts),
        gaps=tuple(gaps),
        notes=(
            "Acceptance above VAH / rejection back inside value is only observable after the open.",
        ),
        uncertainty="Profile is of the futures proxy, not the cash index itself.",
    )
