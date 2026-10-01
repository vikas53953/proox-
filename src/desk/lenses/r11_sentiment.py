"""R11 Sentiment: deduplicated origins, entity/sector tone, contradictions.

Tone does not guarantee direction. A contradiction = the same entity reported with
opposite stance by different origin stories."""

from collections import defaultdict
from dataclasses import dataclass

from desk.core.facts import DataClass
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.context import LensContext, dec, missing, mk_fact
from desk.lenses.r01_news import unique_stories

SENTIMENT_VERSION = "tone-count-v1"


@dataclass(frozen=True)
class Contradiction:
    entity: str
    positive: tuple[str, ...]  # origin ids
    negative: tuple[str, ...]

    def describe(self) -> str:
        return (
            f"CONTRADICTION on {self.entity}: positive per {', '.join(self.positive)}; "
            f"negative per {', '.join(self.negative)}"
        )


def find_contradictions(records: list[dict]) -> list[Contradiction]:
    by_entity: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for r in unique_stories(records):
        for e in r.get("entities", []):
            by_entity[e][r["stance"]].add(r["origin_id"])
    out = []
    for entity in sorted(by_entity):
        pos, neg = by_entity[entity]["positive"], by_entity[entity]["negative"]
        if pos and neg:
            out.append(Contradiction(entity, tuple(sorted(pos)), tuple(sorted(neg))))
    return out


def build(ctx: LensContext) -> LensResult:
    ds = ctx.ds(DatasetKind.NEWS)
    if ds is None:
        return LensResult(
            lens=LensId.R11, gaps=(missing(DatasetKind.NEWS, ctx, "no sentiment can be assessed"),)
        )
    stories = unique_stories(ds.records)
    note = f"method {SENTIMENT_VERSION}"
    facts = [
        mk_fact(
            ds,
            "Unique origin stories",
            dec(len(stories)),
            "count",
            "MARKET",
            DataClass.DERIVED,
            note=note,
        ),
        mk_fact(
            ds,
            "Reposts collapsed",
            dec(len(ds.records) - len(stories)),
            "count",
            "MARKET",
            DataClass.DERIVED,
            note=note,
        ),
    ]
    tone: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in stories:
        for s in r.get("sectors", []):
            tone[s][r["stance"]] += 1
    for sector in sorted(tone):
        t = tone[sector]
        facts.append(
            mk_fact(
                ds,
                f"{sector} tone (pos/neg/neutral)",
                f"{t['positive']}/{t['negative']}/{t['neutral']}",
                "count",
                sector,
                DataClass.DERIVED,
                note=note,
            )
        )
    notes = [c.describe() for c in find_contradictions(ds.records)]
    return LensResult(
        lens=LensId.R11,
        facts=tuple(facts),
        notes=tuple(notes),
        uncertainty="Tone does not guarantee direction.",
    )
