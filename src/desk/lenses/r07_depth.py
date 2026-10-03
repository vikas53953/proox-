"""R07 Liquidity / depth: entitled depth tier, prior-session spread/size statistics,
and (09:12 addendum only) indicative pre-open equilibrium. Resting liquidity can cancel."""

from desk.core.facts import DataClass, Gap
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.context import (
    LensContext,
    ReportKind,
    dec,
    missing,
    mk_fact,
    session_fact_class,
    stale_gap,
)

AUCTION_NOT_YET = Gap(
    topic="pre-open equilibrium",
    data_class=DataClass.NOT_APPLICABLE,
    reason="pre-open order entry starts 09:00 IST; no equilibrium at 08:45",
)


def build(ctx: LensContext) -> LensResult:
    facts, gaps = [], []
    cap = ctx.capabilities.get(DatasetKind.DEPTH)
    ds = ctx.ds(DatasetKind.DEPTH) if cap.granted else None
    if ds is None:
        gaps.append(missing(DatasetKind.DEPTH, ctx, "no spread/size statistics"))
    else:
        cls, why = session_fact_class(ctx, DatasetKind.DEPTH, ds)
        for r in ds.records:
            inst = r["instrument"]
            facts += [
                mk_fact(
                    ds,
                    f"{inst} median spread",
                    dec(r["median_spread_bps"]),
                    "bps",
                    inst,
                    cls,
                    note=why,
                ),
                mk_fact(
                    ds,
                    f"{inst} median best-level size",
                    dec(r["median_l1_qty"]),
                    "shares",
                    inst,
                    cls,
                    note=why,
                ),
            ]
        if why:
            gaps.append(stale_gap(DatasetKind.DEPTH, why))
    if ctx.kind is ReportKind.MORNING:
        gaps.append(AUCTION_NOT_YET)
    else:
        pre = ctx.ds(DatasetKind.PRE_OPEN)
        pre_stale = ctx.staleness(DatasetKind.PRE_OPEN, pre) if pre is not None else None
        if pre is None:
            gaps.append(missing(DatasetKind.PRE_OPEN, ctx, "no indicative auction data"))
        elif pre_stale:
            # an old auction is not today's indicative price: no value, only the gap
            gaps.append(stale_gap(DatasetKind.PRE_OPEN, pre_stale, "no indicative auction data"))
        else:
            for r in pre.records:
                facts.append(
                    mk_fact(
                        pre,
                        f"{r['instrument']} indicative equilibrium price",
                        dec(r["iep"]),
                        r.get("unit", "INR"),
                        r["instrument"],
                        DataClass.INDICATIVE_AUCTION,
                    )
                )
                facts.append(
                    mk_fact(
                        pre,
                        f"{r['instrument']} buy/sell imbalance",
                        dec(r["imbalance_qty"]),
                        "shares",
                        r["instrument"],
                        DataClass.INDICATIVE_AUCTION,
                    )
                )
    return LensResult(
        lens=LensId.R07,
        facts=tuple(facts),
        gaps=tuple(gaps),
        notes=(
            f"Entitled depth tier: {cap.tier or 'none'} ({cap.rights_note}).",
            "Visible resting liquidity can be cancelled before it trades.",
        ),
    )
