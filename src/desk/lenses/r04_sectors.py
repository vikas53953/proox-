"""R04 Sector map: prior-session returns, breadth, weights, relative strength.

Today's sector returns do not exist before trading starts.
"""

from desk.core.facts import DataClass, Gap
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.context import LensContext, dec, missing, mk_fact, session_fact_class, stale_gap

TODAY_NA = Gap(
    topic="today's sector returns",
    data_class=DataClass.NOT_APPLICABLE,
    reason="cash market opens 09:15 IST; no intraday sector returns exist yet",
)

NO_WEIGHTS = Gap(
    topic="sector weights",
    data_class=DataClass.UNAVAILABLE,
    reason="Nifty 50 weight per sector is not in the current feed's sector data",
)


def build(ctx: LensContext) -> LensResult:
    ds = ctx.ds(DatasetKind.SECTORS)
    if ds is None:
        return LensResult(lens=LensId.R04, gaps=(missing(DatasetKind.SECTORS, ctx), TODAY_NA))
    cls, why = session_fact_class(ctx, DatasetKind.SECTORS, ds)
    index_ret = dec(ds.meta["index_return_pct"])
    facts = []
    for r in sorted(ds.records, key=lambda r: dec(r["return_pct"]), reverse=True):
        s = f"NIFTY {r['sector'].upper()} (sector index)"
        ret = dec(r["return_pct"])
        facts += [
            mk_fact(ds, f"{r['sector']} return", ret, "%", s, cls, note=why),
            mk_fact(
                ds,
                f"{r['sector']} vs Nifty 50",
                ret - index_ret,
                "% points",
                s,
                DataClass.STALE if why else DataClass.DERIVED,
                note=why,
            ),
            mk_fact(
                ds,
                f"{r['sector']} breadth (adv/dec)",
                f"{r['advances']}/{r['declines']}",
                "count",
                s,
                cls,
                note=why,
            ),
        ]
        if r.get("weight_pct") is not None:  # a feed without weights shows a gap instead
            facts.append(
                mk_fact(
                    ds,
                    f"{r['sector']} weight in Nifty 50",
                    dec(r["weight_pct"]),
                    "%",
                    s,
                    cls,
                    note=why,
                )
            )
    gaps = [TODAY_NA] + ([stale_gap(DatasetKind.SECTORS, why)] if why else [])
    if any(r.get("weight_pct") is None for r in ds.records):
        gaps.append(NO_WEIGHTS)
    return LensResult(lens=LensId.R04, facts=tuple(facts), gaps=tuple(gaps))
