"""R08 Order flow. Real delta needs licensed trade events with aggressor side.
Price-only trades -> tick-rule PROXY with limits. No rights -> UNAVAILABLE, no number."""

from desk.core.facts import DataClass, Gap
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.context import LensContext, dec, missing, mk_fact, session_fact_class, stale_gap
from desk.quant.orderflow import ORDERFLOW_VERSION, TICK_RULE_LIMITS, Trade, tick_rule_delta

TODAY_NA = Gap(
    topic="today's order flow",
    data_class=DataClass.NOT_APPLICABLE,
    reason="continuous trading starts 09:15 IST; today's executed flow does not exist",
)


def build(ctx: LensContext) -> LensResult:
    cap = ctx.capabilities.get(DatasetKind.TRADES)
    ds = ctx.ds(DatasetKind.TRADES) if cap.granted else None
    if ds is None:
        return LensResult(
            lens=LensId.R08,
            gaps=(missing(DatasetKind.TRADES, ctx, "no delta/footprint; not estimated"), TODAY_NA),
        )
    cls, why = session_fact_class(ctx, DatasetKind.TRADES, ds)
    inst = ds.meta["instrument"]
    gaps = [TODAY_NA] + ([stale_gap(DatasetKind.TRADES, why)] if why else [])
    if cap.tier == "with-aggressor":
        delta = sum(
            (dec(t["qty"]) * (1 if t["aggressor"] == "buy" else -1) for t in ds.records), dec(0)
        )
        fact = mk_fact(
            ds, "Prior session delta (aggressor-flagged)", delta, "contracts", inst, cls, note=why
        )
    else:
        res = tick_rule_delta([Trade(dec(t["price"]), dec(t["qty"])) for t in ds.records])
        fact = mk_fact(
            ds,
            "Prior session delta (tick-rule estimate)",
            res.delta,
            "contracts",
            inst,
            DataClass.STALE if why else DataClass.PROXY,
            note=why
            or f"{TICK_RULE_LIMITS}; unclassified "
            f"{res.unclassified_volume}; method {ORDERFLOW_VERSION}",
        )
    return LensResult(
        lens=LensId.R08,
        facts=(fact,),
        gaps=tuple(gaps),
        notes=("Snapshot quotes/depth are not executed flow.",),
    )
