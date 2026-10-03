"""R02 US / Asia / global futures. Each value says open/closed, contract, time and delay.

A global cue is not a certain Indian opening prediction.
"""

from datetime import datetime

from desk.core.facts import DataClass
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.context import (
    GLOBAL_MAX_AGE,
    LensContext,
    dec,
    fmt_age,
    missing,
    mk_fact,
    stale_gap,
)
from desk.quant.options import TWO_DP


def build(ctx: LensContext) -> LensResult:
    facts, gaps = [], []
    ds = ctx.ds(DatasetKind.GLOBAL)
    if ds is None:
        gaps.append(missing(DatasetKind.GLOBAL, ctx, "no global cue"))
    else:
        for r in ds.records:
            as_of = datetime.fromisoformat(r["as_of"])
            if as_of > ctx.cutoff:
                continue  # quote after cutoff: not part of this report (no look-ahead)
            age = ctx.age(as_of)
            status = r["session_status"]
            name = f"{r['instrument']} ({r['contract']}, {r['venue']}, {status.upper()})"
            delay = f"delay {r['delay_min']} min"
            if age > GLOBAL_MAX_AGE[status]:
                why = f"{r['instrument']} last update {fmt_age(age)} before cutoff"
                gaps.append(stale_gap(DatasetKind.GLOBAL, why, "cue may be outdated"))
                cls, note = DataClass.STALE, f"{why}; {delay}"
            else:
                cls = DataClass.OVERNIGHT if status == "closed" else DataClass.LIVE_GLOBAL
                note = delay
            last, prev = dec(r["last"]), dec(r["prev_close"])
            facts.append(
                mk_fact(
                    ds,
                    f"{name} last",
                    last,
                    r["unit"],
                    r["instrument"],
                    cls,
                    note=note,
                    as_of=as_of,
                )
            )
            if prev:
                chg = ((last - prev) / prev * 100).quantize(TWO_DP)
                facts.append(
                    mk_fact(
                        ds,
                        f"{name} change vs prev close",
                        chg,
                        "%",
                        r["instrument"],
                        DataClass.STALE if cls is DataClass.STALE else DataClass.DERIVED,
                        note=note if cls is DataClass.STALE else "",
                        as_of=as_of,
                    )
                )
    gift = (
        ctx.ds(DatasetKind.GIFT_NIFTY)
        if ctx.capabilities.get(DatasetKind.GIFT_NIFTY).granted
        else None
    )
    if gift is None:
        gaps.append(missing(DatasetKind.GIFT_NIFTY, ctx, "no licensed GIFT Nifty cue"))
    else:
        for r in gift.records:
            facts.append(
                mk_fact(
                    gift,
                    f"GIFT Nifty {r['contract']} last",
                    dec(r["last"]),
                    "points",
                    "GIFT NIFTY",
                    DataClass.LIVE_GLOBAL,
                    note=f"delay {r['delay_min']} min",
                )
            )
    return LensResult(
        lens=LensId.R02,
        facts=tuple(facts),
        gaps=tuple(gaps),
        notes=("A global cue is not a certain Indian opening prediction.",),
    )
