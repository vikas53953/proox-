"""Worker: one job per call. Produce the report, then — in ONE fenced transaction —
mark the job DONE, store the report per opted-in tenant and queue its parts.

Failures are never silent (E07): after the last attempt, or past the hard stop,
opted-in tenants are told plainly that there is no report today. A previous day's
report is never offered as today's.
"""

import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from desk.agents.model import ModelAdapter
from desk.db.models import Job, StoredReport, Tenant
from desk.feeds.base import FeedAdapter
from desk.jobs.queue import StaleLeaseError, complete, expire_overdue, fail, lease_next
from desk.lenses.context import ReportKind
from desk.market_calendar import TradingCalendar
from desk.outbox.notices import enqueue_failure_notice, enqueue_report
from desk.outbox.parts import report_parts
from desk.pipeline import NoReport, run_report
from desk.report.model import IncompleteReportError, ReviewRejectedError
from desk.tenancy import TenantScope

KIND_TO_REPORT = {"MORNING_REPORT": ReportKind.MORNING, "AUCTION_ADDENDUM": ReportKind.AUCTION}


@dataclass
class Deps:
    calendar: TradingCalendar
    feed_for: Callable[[date], FeedAdapter]
    model: ModelAdapter
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    elapsed: Callable[[], float] = field(default=_time.monotonic)


def _opted_in(session: Session) -> list[Tenant]:
    return list(session.execute(select(Tenant).where(Tenant.opt_in_state == "yes")).scalars())


def _notify_failure(session: Session, kind: str, day: date, category: str, now: datetime) -> None:
    for tenant in _opted_in(session):
        enqueue_failure_notice(session, tenant, day, kind, category, now)


def _fail(factory, lease, category: str, error: str, now: datetime, retryable: bool) -> str:
    with factory() as s:
        try:
            final = fail(s, lease, f"[{category}] {error}", now, retryable)
        except StaleLeaseError:
            return "stale"
        if final:
            _notify_failure(s, lease.kind, lease.trading_date, category, now)
        s.commit()
        return f"failed:{category}" + (":final" if final else ":retry")


def run_one(factory: sessionmaker[Session], deps: Deps, worker: str) -> str:
    now = deps.clock()
    with factory() as s:
        for job in expire_overdue(s, now):
            _notify_failure(s, job.kind, job.trading_date, "deadline", now)
        lease = lease_next(s, worker, now)
        s.commit()
    if lease is None:
        return "idle"

    started = deps.elapsed()
    try:
        result = run_report(
            calendar=deps.calendar,
            feed=deps.feed_for(lease.trading_date),
            model=deps.model,
            trading_date=lease.trading_date,
            kind=KIND_TO_REPORT[lease.kind],
        )
    except (ReviewRejectedError, IncompleteReportError) as exc:
        return _fail(factory, lease, "review", str(exc), deps.clock(), retryable=False)
    except Exception as exc:  # feed / model / anything external: retry, then tell users
        return _fail(
            factory, lease, "feed", f"{type(exc).__name__}: {exc}", deps.clock(), retryable=True
        )
    if deps.elapsed() - started > lease.budget_seconds:
        return _fail(
            factory,
            lease,
            "budget",
            "time budget exceeded; result discarded",
            deps.clock(),
            retryable=True,
        )

    ready_at = deps.clock()
    with factory() as s:
        try:
            if isinstance(result, NoReport):
                complete(s, lease, {"no_report": result.reason}, ready_at)
                s.commit()
                return "no_report"
            complete(
                s,
                lease,
                {
                    "report_id": result.id,
                    "version": result.version,
                    "hash": result.content_hash,
                    "late": ready_at > lease.deadline_at,
                },
                ready_at,
            )
            parts = report_parts(result, ready_at, lease.deadline_at)
            for tenant in _opted_in(s):
                scope = TenantScope(s, tenant.id)
                if not s.execute(
                    select(StoredReport.id).where(
                        StoredReport.tenant_id == tenant.id,
                        StoredReport.report_id == result.id,
                        StoredReport.version == result.version,
                    )
                ).first():
                    scope.save_report(result, ready_at)
                enqueue_report(s, tenant, result, parts, ready_at)
            s.commit()
        except StaleLeaseError:
            s.rollback()
            return "stale"
    return "done:late" if ready_at > lease.deadline_at else "done"


def job_state(factory: sessionmaker[Session], kind: str, day: date) -> Job | None:
    with factory() as s:
        return s.execute(
            select(Job).where(Job.kind == kind, Job.trading_date == day)
        ).scalar_one_or_none()
