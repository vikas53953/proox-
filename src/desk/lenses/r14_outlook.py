"""R14 Conditional outlook: base/up/down paths with observable triggers, invalidation,
qualitative confidence, contrary evidence, watch names and event windows.

The chief role drafts paths through the model adapter; the draft is validated against
RC06 (no certainty language, no numeric probabilities) before it can enter a report."""

import json

from desk.agents.model import DataEnvelope
from desk.core.facts import DataClass, Gap
from desk.core.lens import LensId, LensResult
from desk.core.scenario import Scenario
from desk.feeds.base import DatasetKind
from desk.lenses.context import LensContext
from desk.lenses.r11_sentiment import find_contradictions

LEVEL_LABELS = {
    "Prior session high": "prior_high",
    "Prior session low": "prior_low",
    "Value area high (VAH)": "vah",
    "Value area low (VAL)": "val",
}
SCHEDULED = "Scheduled: "


def _facts(ctx: LensContext, lens: LensId) -> tuple:
    result = ctx.results.get(lens)
    return result.facts if result else ()


def scenarios_from_model(raw: str) -> tuple[Scenario, ...]:
    return tuple(Scenario.model_validate(item) for item in json.loads(raw))


def build(ctx: LensContext) -> tuple[LensResult, tuple[Scenario, ...]]:
    if ctx.model is None:
        raise RuntimeError("R14 needs a model adapter (mock while G01 is BLOCKED)")
    ref_facts = [
        f
        for f in (*_facts(ctx, LensId.R06), *_facts(ctx, LensId.R12))
        if f.label in LEVEL_LABELS and f.data_class is not DataClass.STALE
    ]
    levels = {
        LEVEL_LABELS[f.label]: {"value": str(f.value), "instrument": f.instrument}
        for f in ref_facts
    }
    watch = sorted(
        {f.instrument for f in _facts(ctx, LensId.R05) if f.label.endswith("prior close")}
    )
    events = [
        f"{f.label.removeprefix(SCHEDULED)} {f.value} IST"
        for f in _facts(ctx, LensId.R03)
        if f.label.startswith(SCHEDULED)
    ]
    news = ctx.datasets.get(DatasetKind.NEWS)
    contrary = [c.describe() for c in find_contradictions(news.records)] if news else []

    envelope = DataEnvelope(
        {"levels": levels, "watch": watch, "events": events, "contrary": contrary}
    )
    scenarios = scenarios_from_model(ctx.model.complete("chief", "scenarios", envelope))

    gaps = []
    if len(levels) < len(LEVEL_LABELS):
        gaps.append(
            Gap(
                topic="reference levels",
                data_class=DataClass.UNAVAILABLE,
                reason="prior-session levels or value area missing/stale; "
                "paths are qualitative only",
            )
        )
    notes = tuple(
        f"{s.path}: {s.condition}. Trigger: {s.trigger}. Invalidation: "
        f"{s.invalidation}. Confidence: {s.confidence}."
        for s in scenarios
    )
    notes += tuple(f"Contrary evidence: {c}" for c in contrary)
    result = LensResult(
        lens=LensId.R14,
        facts=tuple(ref_facts),
        gaps=tuple(gaps),
        notes=notes,
        uncertainty="Conditional paths, not predictions. Confidence is qualitative.",
    )
    return result, scenarios
