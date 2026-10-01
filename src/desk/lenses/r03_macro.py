"""R03 Oil / macro: levels, scheduled events, plausible India transmission paths.

Correlation is not causation; contrary evidence is listed next to each path.
"""

from desk.core.facts import DataClass
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.context import LensContext, dec, missing, mk_fact, stale_gap


def build(ctx: LensContext) -> LensResult:
    ds = ctx.ds(DatasetKind.MACRO)
    if ds is None:
        return LensResult(
            lens=LensId.R03, gaps=(missing(DatasetKind.MACRO, ctx, "oil/FX/rates context missing"),)
        )
    stale = ctx.staleness(DatasetKind.MACRO, ds)
    cls = DataClass.STALE if stale else DataClass.OVERNIGHT
    facts = [
        mk_fact(ds, r["label"], dec(r["value"]), r["unit"], r["instrument"], cls, note=stale or "")
        for r in ds.records
    ]
    for ev in ds.meta.get("events", []):
        facts.append(
            mk_fact(
                ds,
                f"Scheduled: {ev['name']}",
                ev["time_ist"],
                "IST time",
                ev.get("region", "GLOBAL"),
                DataClass.OVERNIGHT,
            )
        )
    notes = [
        f"Path: {p['path']} | Contrary: {p['contrary']}" for p in ds.meta.get("transmission", [])
    ]
    gaps = [stale_gap(DatasetKind.MACRO, stale)] if stale else []
    return LensResult(
        lens=LensId.R03,
        facts=tuple(facts),
        gaps=tuple(gaps),
        notes=tuple(notes),
        uncertainty="Transmission paths are plausible channels, not causation.",
    )
