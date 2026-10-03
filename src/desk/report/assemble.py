"""Assembly: the completeness gate (all 15 lenses, in order), review, version and hash."""

from datetime import date, datetime

from desk.agents.reviewer import Review, ReviewStatus
from desk.core.lens import LensId, LensResult
from desk.core.scenario import Scenario
from desk.feeds.base import TERMS_GAP_TOPIC
from desk.lenses.context import ReportKind
from desk.report.model import IncompleteReportError, Report, ReviewRejectedError


def top_gaps(lenses: list[LensResult]) -> tuple[str, ...]:
    """Essential gaps for the first message: every degrading gap, one line per reason,
    naming every lens it affects."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for r in lenses:
        for g in r.gaps:
            if r.lens is LensId.R15 and g.topic != TERMS_GAP_TOPIC:
                continue  # R15 restates the same gaps per dataset; only its terms gap is new
            if g.degrades:
                grouped.setdefault((str(g.data_class), g.reason), []).append(r.lens.value)
    return tuple(
        f"{cls}: {reason} (affects {', '.join(dict.fromkeys(ids))})"
        for (cls, reason), ids in grouped.items()
    )


def assemble(
    *,
    kind: ReportKind,
    trading_date: date,
    cutoff: datetime,
    version: int,
    is_mock: bool,
    feed: str,
    model: str,
    calendar_version: str,
    method_versions: dict[str, str],
    lenses: list[LensResult],
    scenarios: tuple[Scenario, ...],
    review: Review,
) -> Report:
    got = [r.lens for r in lenses]
    if got != list(LensId):
        missing = [lid.value for lid in LensId if lid not in got]
        raise IncompleteReportError(f"lenses missing/out of order: missing={missing}")
    if review.status is ReviewStatus.REJECTED:
        raise ReviewRejectedError("; ".join(review.findings))
    prefix = "MOCK-" if is_mock else ""
    report = Report(
        id=f"{prefix}{trading_date.isoformat()}-{kind.value}",
        kind=kind,
        trading_date=trading_date,
        cutoff=cutoff,
        version=version,
        is_mock=is_mock,
        feed=feed,
        model=model,
        calendar_version=calendar_version,
        method_versions=method_versions,
        lenses=tuple(lenses),
        scenarios=scenarios,
        top_gaps=top_gaps(lenses),
        review_status=review.status,
        review_findings=review.findings,
    )
    return report.model_copy(update={"content_hash": report.compute_hash()})
