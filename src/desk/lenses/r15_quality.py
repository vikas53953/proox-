"""R15 Data / quality summary: every dataset's source, as-of and freshness, every lens's
status, and the gaps — so no fluent prose can hide a feed failure. RESEARCH mode only."""

from desk.core.facts import DataClass, Gap
from desk.core.lens import LensId, LensResult
from desk.feeds.base import TERMS_GAP_TOPIC, DatasetKind
from desk.lenses.context import LensContext, missing, mk_fact
from desk.market_calendar import fmt_ist

OPTIONAL_FOR_MORNING = {DatasetKind.PRE_OPEN}


def build(ctx: LensContext) -> LensResult:
    facts, gaps = [], []
    skipped = OPTIONAL_FOR_MORNING if ctx.kind.value == "MORNING" else set()
    for kind in DatasetKind:
        if kind in skipped:
            continue
        ds = ctx.datasets.get(kind)
        if ds is None:
            gaps.append(missing(kind, ctx))
            continue
        why = ctx.staleness(kind, ds)
        facts.append(
            mk_fact(
                ds,
                f"Dataset {kind.value} as of",
                fmt_ist(ds.as_of),
                "timestamp",
                f"dataset {kind.value}",
                DataClass.STALE if why else DataClass.DERIVED,
                note=why or "",
            )
        )
    # Data read under terms the owner has not confirmed: one gap per distinct terms note.
    unconfirmed: dict[str, list[str]] = {}
    for kind in DatasetKind:
        note = ctx.capabilities.get(kind).terms_unconfirmed
        if note and ctx.datasets.get(kind) is not None and kind not in skipped:
            unconfirmed.setdefault(note, []).append(kind.value)
    for note, kinds in unconfirmed.items():
        gaps.append(
            Gap(
                topic=TERMS_GAP_TOPIC,
                data_class=DataClass.UNAVAILABLE,
                reason=f"{note}; applies to {', '.join(kinds)}",
                effect="values shown for research review only until the owner confirms terms",
            )
        )
    notes = [f"{lid} {r.title}: {r.status}" for lid, r in ctx.results.items()]
    notes.append("Mode: RESEARCH. No simulated account, no orders, no broker.")
    return LensResult(lens=LensId.R15, facts=tuple(facts), gaps=tuple(gaps), notes=tuple(notes))
