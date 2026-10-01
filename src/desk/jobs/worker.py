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
from desk.outbox.addendum import addendum_text
from desk.outbox.notices import enqueue_failure_notice, enqueue_message, enqueue_report
from desk.outbox.parts import delivery_plan
from desk.pipeline import NoReport, run_report
from desk.render.charts import report_charts
from desk.render.pdf import FontBlockedError, render_pdf
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
    if isinstance(result, NoReport):
        with factory() as s:
            try:
                complete(s, lease, {"no_report": result.reason}, ready_at)
                s.commit()
            except StaleLeaseError:
                return "stale"
        return "no_report"
    if lease.kind == "AUCTION_ADDENDUM":
        return _publish_addendum(factory, lease, result, ready_at)

    charts = report_charts(result)  # rendering happens before the fenced transaction
    try:
        pdf = render_pdf(result, charts)
    except FontBlockedError:
        pdf = None  # real reports: font BLOCKED -> full text parts instead
    parts = delivery_plan(result, ready_at, lease.deadline_at, charts, pdf)
    with factory() as s:
        try:
            complete(
                s,
                lease,
                {
                    "report_id": result.id,
                    "version": result.version,
                    "hash": result.content_hash,
                    "parts": len(parts),
                    "pdf": pdf is not None,
                    "late": ready_at > lease.deadline_at,
                },
                ready_at,
            )
            for tenant in _opted_in(s):
                if not s.execute(
                    select(StoredReport.id).where(
                        StoredReport.tenant_id == tenant.id,
                        StoredReport.report_id == result.id,
                        StoredReport.version == result.version,
                    )
                ).first():
                    TenantScope(s, tenant.id).save_report(result, ready_at)
                enqueue_report(s, tenant, result, parts, ready_at)
            s.commit()
        except StaleLeaseError:
            s.rollback()
            return "stale"
    return "done:late" if ready_at > lease.deadline_at else "done"


def _morning_ref(session: Session, day: date) -> str | None:
    job = session.execute(
        select(Job).where(
            Job.kind == "MORNING_REPORT", Job.trading_date == day, Job.state == "DONE"
        )
    ).scalar_one_or_none()
    if job is None or not job.result or "report_id" not in job.result:
        return None
    return f"{job.result['report_id']} v{job.result['version']}"


def _publish_addendum(factory, lease, auction, ready_at: datetime) -> str:
    with factory() as s:
        try:
            complete(
                s,
                lease,
                {"report_id": auction.id, "version": auction.version, "hash": auction.content_hash},
                ready_at,
            )
            text = addendum_text(auction, _morning_ref(s, lease.trading_date))
            for tenant in _opted_in(s):
                enqueue_message(
                    s,
                    tenant,
                    key=f"addendum:{auction.id}:v{auction.version}:{tenant.id}",
                    kind="addendum",
                    body=text,
                    now=ready_at,
                    trading_date=lease.trading_date,
                    report=auction,
                    expires_at=lease.hard_stop_at,
                )
            s.commit()
        except StaleLeaseError:
            s.rollback()
            return "stale"
    return "done:addendum"


def job_state(factory: sessionmaker[Session], kind: str, day: date) -> Job | None:
    with factory() as s:
        return s.execute(
            select(Job).where(Job.kind == kind, Job.trading_date == day)
        ).scalar_one_or_none()
