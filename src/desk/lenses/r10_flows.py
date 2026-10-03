"""R10 Funding / flows: dated provisional FII/DII cash flows, RBI liquidity, rates.

NSE futures/options have no crypto-style perpetual funding rate. Market funding is kept
separate from corporate fund-raising."""

from desk.core.facts import DataClass, Gap
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.context import LensContext, dec, missing, mk_fact, session_fact_class, stale_gap

NO_PERP = Gap(
    topic="perpetual funding rate",
    data_class=DataClass.NOT_APPLICABLE,
    reason="ordinary NSE futures/options have no perpetual funding rate",
)


def build(ctx: LensContext) -> LensResult:
    ds = ctx.ds(DatasetKind.FLOWS)
    if ds is None:
        return LensResult(lens=LensId.R10, gaps=(missing(DatasetKind.FLOWS, ctx), NO_PERP))
    cls, why = session_fact_class(ctx, DatasetKind.FLOWS, ds)
    facts = []
    for r in ds.records:
        tag = " (provisional)" if r.get("provisional") else ""
        facts.append(
            mk_fact(
                ds, f"{r['label']}{tag}", dec(r["value"]), r["unit"], r["instrument"], cls, note=why
            )
        )
    gaps = [NO_PERP] + ([stale_gap(DatasetKind.FLOWS, why)] if why else [])
    if not ds.meta.get("margin_financing_sourced", False):
        gaps.append(
            Gap(
                topic="margin financing",
                data_class=DataClass.UNAVAILABLE,
                reason="no sourced margin-financing data in current feed",
            )
        )
    return LensResult(
        lens=LensId.R10,
        facts=tuple(facts),
        gaps=tuple(gaps),
        notes=("Provisional flow figures can be revised.",),
    )
