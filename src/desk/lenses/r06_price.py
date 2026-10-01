"""R06 Price action: prior-session high/low/close from completed bars only.

The cash index has no traded volume, so volume comes from index futures, marked PROXY.
"""

from decimal import Decimal

from desk.core.facts import DataClass
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.bars_io import to_bars
from desk.lenses.context import LensContext, missing, mk_fact, session_fact_class, stale_gap
from desk.quant.bars import latest_completed_session
from desk.quant.levels import LEVELS_VERSION, prior_session_levels

VOLUME_PROXY_NOTE = "index futures volume used as proxy; the cash index has no traded volume"


def build(ctx: LensContext) -> LensResult:
    facts, gaps, notes = [], [], []
    ds = ctx.ds(DatasetKind.INDEX_BARS)
    if ds is None:
        gaps.append(missing(DatasetKind.INDEX_BARS, ctx, "no prior high/low/close"))
    else:
        cls, why = session_fact_class(ctx, DatasetKind.INDEX_BARS, ds)
        lv = prior_session_levels(to_bars(ds), ctx.cutoff)
        if lv is None:
            gaps.append(missing(DatasetKind.INDEX_BARS, ctx, "no completed bars"))
        else:
            inst = ds.meta["instrument"]
            derived = DataClass.STALE if why else DataClass.DERIVED
            facts += [
                mk_fact(ds, "Prior session high", lv.high, "points", inst, cls, note=why),
                mk_fact(ds, "Prior session low", lv.low, "points", inst, cls, note=why),
                mk_fact(ds, "Prior session close", lv.close, "points", inst, cls, note=why),
                mk_fact(
                    ds,
                    "Prior session range",
                    lv.range_points,
                    "points",
                    inst,
                    derived,
                    note=why or f"method {LEVELS_VERSION}",
                ),
                mk_fact(
                    ds,
                    "Close location in range (0=low, 1=high)",
                    lv.close_location,
                    "ratio",
                    inst,
                    derived,
                    note=why or f"method {LEVELS_VERSION}",
                ),
            ]
            if why:
                gaps.append(stale_gap(DatasetKind.INDEX_BARS, why))
            if lv.close_location >= 0.7:
                notes.append("Closed in the upper part of its range.")
            elif lv.close_location <= 0.3:
                notes.append("Closed in the lower part of its range.")
            else:
                notes.append("Closed mid-range.")
    fut = ctx.ds(DatasetKind.FUT_BARS)
    if fut is None:
        gaps.append(missing(DatasetKind.FUT_BARS, ctx, "no volume proxy"))
    else:
        fcls, fwhy = session_fact_class(ctx, DatasetKind.FUT_BARS, fut)
        session = latest_completed_session(to_bars(fut), ctx.cutoff)
        vol = sum((b.volume or Decimal(0) for b in session), Decimal(0)) if session else None
        if vol is not None:
            facts.append(
                mk_fact(
                    fut,
                    "Prior session volume (futures proxy)",
                    vol,
                    "contracts",
                    fut.meta["instrument"],
                    DataClass.STALE if fwhy else DataClass.PROXY,
                    note=fwhy or VOLUME_PROXY_NOTE,
                )
            )
        if fwhy:
            gaps.append(stale_gap(DatasetKind.FUT_BARS, fwhy))
    return LensResult(
        lens=LensId.R06,
        facts=tuple(facts),
        gaps=tuple(gaps),
        notes=tuple(notes),
        uncertainty="Levels are references from completed bars, not targets.",
    )
