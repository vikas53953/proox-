"""Reviewer role: deterministic checks before a report may be assembled.

REJECTED blocks the report entirely. DEGRADED is allowed but must be visible."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from desk.core.lens import LensId, LensResult, LensStatus
from desk.core.scenario import Scenario


class ReviewStatus(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class Review:
    status: ReviewStatus
    findings: tuple[str, ...]


def review(
    lenses: list[LensResult],
    scenarios: tuple[Scenario, ...],
    cutoff: datetime,
    is_mock_report: bool,
) -> Review:
    reject, degrade = [], []
    if [r.lens for r in lenses] != list(LensId):
        reject.append("lenses missing or out of order (need R01-R15)")
    for r in lenses:
        for f in r.facts:
            if f.as_of > cutoff:
                reject.append(f"{r.lens} '{f.label}' timestamp after cutoff (look-ahead)")
            if f.source.is_mock and not is_mock_report:
                reject.append(f"{r.lens} '{f.label}' mock source in a non-mock report")
        if r.status is not LensStatus.COMPLETE:
            degrade.append(f"{r.lens} {r.title}: {r.status}")
    if {s.path for s in scenarios} != {"BASE", "UP", "DOWN"}:
        reject.append("R14 needs exactly base, up and down paths")
    r11 = next((r for r in lenses if r.lens is LensId.R11), None)
    contradictions = [n for n in (r11.notes if r11 else ()) if n.startswith("CONTRADICTION")]
    if contradictions and not all(s.contrary_evidence for s in scenarios):
        reject.append("contradictions found in R11 but not carried into R14 contrary evidence")
    if reject:
        return Review(ReviewStatus.REJECTED, tuple(reject))
    return Review(ReviewStatus.DEGRADED if degrade else ReviewStatus.READY, tuple(degrade))
