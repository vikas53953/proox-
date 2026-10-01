"""Lease / complete / fail / cancel with fencing epochs (RC10).

A lease is (job id, epoch). complete() and fail() only succeed if the job still has
that epoch and is LEASED; otherwise StaleLeaseError and the caller writes nothing.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from desk.db.models import Job

RETRY_DELAY = timedelta(minutes=1)


class StaleLeaseError(RuntimeError):
    """This worker's lease is no longer valid; its result must be discarded."""


@dataclass(frozen=True)
class Lease:
    job_id: uuid.UUID
    epoch: int
    kind: str
    trading_date: date
    deadline_at: datetime
    budget_seconds: int
    attempts: int
    max_attempts: int


def lease_next(
    session: Session, worker: str, now: datetime, lease_for: timedelta = timedelta(minutes=10)
) -> Lease | None:
    while True:
        job = session.execute(
            select(Job)
            .where(
                (
                    (Job.state == "PENDING")
                    & (Job.not_before <= now)
                    & ((Job.next_attempt_at.is_(None)) | (Job.next_attempt_at <= now))
                )
                | ((Job.state == "LEASED") & (Job.lease_until < now))
            )
            .order_by(Job.deadline_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if job is None:
            return None
        if job.attempts >= job.max_attempts:  # workers keep dying on it: stop, say so
            job.state, job.lease_epoch = "FAILED", job.lease_epoch + 1
            job.last_error, job.updated_at = f"gave up after {job.attempts} attempts", now
            continue
        job.state, job.lease_owner, job.lease_until = "LEASED", worker, now + lease_for
        job.lease_epoch += 1
        job.attempts += 1
        job.updated_at = now
        return Lease(
            job.id,
            job.lease_epoch,
            job.kind,
            job.trading_date,
            job.deadline_at,
            job.budget_seconds,
            job.attempts,
            job.max_attempts,
        )


def _fenced(session: Session, lease: Lease, now: datetime, **values) -> None:
    res = session.execute(
        update(Job)
        .where(Job.id == lease.job_id, Job.lease_epoch == lease.epoch, Job.state == "LEASED")
        .values(updated_at=now, lease_until=None, **values)
    )
    if res.rowcount != 1:
        raise StaleLeaseError(f"job {lease.job_id} epoch {lease.epoch} no longer leased")


def complete(session: Session, lease: Lease, result: dict, now: datetime) -> None:
    _fenced(session, lease, now, state="DONE", result=result, last_error=None)


def fail(session: Session, lease: Lease, error: str, now: datetime, retryable: bool) -> bool:
    """Returns True if this was the final failure (no more retries)."""
    final = not retryable or lease.attempts >= lease.max_attempts
    _fenced(
        session,
        lease,
        now,
        state="FAILED" if final else "PENDING",
        last_error=error[:1000],
        next_attempt_at=None if final else now + RETRY_DELAY,
    )
    return final


def cancel(session: Session, job_id: uuid.UUID, reason: str, now: datetime) -> None:
    session.execute(
        update(Job)
        .where(Job.id == job_id, Job.state.in_(("PENDING", "LEASED")))
        .values(
            state="CANCELLED", lease_epoch=Job.lease_epoch + 1, last_error=reason, updated_at=now
        )
    )


def expire_overdue(session: Session, now: datetime) -> list[Job]:
    """Jobs past their hard stop are failed (and fenced) so a late worker can't publish."""
    jobs = (
        session.execute(
            select(Job)
            .where(Job.state.in_(("PENDING", "LEASED")), Job.hard_stop_at < now)
            .with_for_update()
        )
        .scalars()
        .all()
    )
    for job in jobs:
        job.state, job.lease_epoch = "FAILED", job.lease_epoch + 1
        job.last_error, job.updated_at = "missed hard stop", now
    return list(jobs)
