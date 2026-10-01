"""R01 Overnight news: original source, time, what changed, credibility, affected names.

Fact, rumor and interpretation are labelled separately. Reposts of one origin story
are collapsed to the original (dedupe key: origin_id).
"""

from datetime import datetime

from desk.core.facts import DataClass, Source
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.context import LensContext, missing, mk_fact, stale_gap

KIND_LABEL = {"fact": "FACT", "rumor": "RUMOR", "interpretation": "INTERPRETATION"}


def unique_stories(records: list[dict]) -> list[dict]:
    """Keep the earliest item per origin_id (the original), drop reposts."""
    best: dict[str, dict] = {}
    for r in records:
        key = r["origin_id"]
        if key not in best or r["published_at"] < best[key]["published_at"]:
            best[key] = r
    return sorted(best.values(), key=lambda r: r["published_at"])


def build(ctx: LensContext) -> LensResult:
    ds = ctx.ds(DatasetKind.NEWS)
    if ds is None:
        return LensResult(
            lens=LensId.R01,
            gaps=(missing(DatasetKind.NEWS, ctx, "no overnight catalysts can be listed"),),
        )
    gaps = []
    stale = ctx.staleness(DatasetKind.NEWS, ds)
    if stale:
        gaps.append(stale_gap(DatasetKind.NEWS, stale, "overnight news may be incomplete"))
    facts, notes = [], []
    for item in unique_stories(ds.records):
        published = datetime.fromisoformat(item["published_at"])
        if published > ctx.cutoff:
            continue  # published after cutoff: not part of this report
        src = Source(id=item["id"], name=item["origin"], url=item["url"], is_mock=ds.source.is_mock)
        tag = KIND_LABEL[item["kind"]]
        facts.append(
            mk_fact(
                ds,
                f"[{tag}] {item['headline']}",
                item["what_changed"],
                "text",
                ", ".join(item.get("entities") or ["MARKET"]),
                DataClass.STALE if stale else DataClass.OVERNIGHT,
                note=f"credibility {item['credibility']}" + (f"; {stale}" if stale else ""),
                source=src,
                as_of=published,
            )
        )
        if item.get("sectors"):
            notes.append(f"{item['headline']} -> sectors: {', '.join(item['sectors'])}")
    if not facts:
        gaps.append(missing(DatasetKind.NEWS, ctx, "no stories before cutoff"))
    return LensResult(
        lens=LensId.R01,
        facts=tuple(facts),
        gaps=tuple(gaps),
        notes=tuple(notes),
        uncertainty="Rumors and interpretations are not confirmed facts.",
    )
