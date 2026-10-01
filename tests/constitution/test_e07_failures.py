"""E07 worker / provider / feed / DB / restart / cost failures give a visible pending,
late or failed state — never yesterday's report as today's (RC10)."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from desk.db.models import Job, Outbox, StoredReport
from desk.jobs.queue import StaleLeaseError, cancel, complete, lease_next
from desk.jobs.scheduler import plan_day
from desk.jobs.worker import job_state, run_one
from desk.outbox.notices import FAILURE_REASONS
from desk.pipeline import run_report
from desk.report.model import ReviewRejectedError
from tests.conftest import MOCK_DAY, feed
from tests.delivery_helpers import Clock, add_tenant, deps, ist

ALICE = "919800000001"


def plan(db, cal, day=MOCK_DAY, at=None, auction=False):
    with db() as s:
        result = plan_day(s, cal, day, at or ist(7, 0), auction_enabled=auction)
        s.commit()
        return result


def jobs(db):
    with db() as s:
        return list(s.execute(select(Job).order_by(Job.kind)).scalars())


def outbox(db, kind=None):
    with db() as s:
        q = select(Outbox)
        if kind:
            q = q.where(Outbox.kind == kind)
        return list(s.execute(q).scalars())


# ---- calendar service -------------------------------------------------------------------


def test_planning_is_idempotent_one_job_per_kind_per_day(db, mock_calendar):
    for _ in range(3):
        plan(db, mock_calendar, auction=True)
    assert sorted((j.kind, j.state) for j in jobs(db)) == [
        ("AUCTION_ADDENDUM", "PENDING"),
        ("MORNING_REPORT", "PENDING"),
    ]
    j = next(j for j in jobs(db) if j.kind == "MORNING_REPORT")
    assert (j.not_before, j.deadline_at, j.hard_stop_at) == (ist(7, 30), ist(8, 45), ist(9, 15))


@pytest.mark.parametrize(
    "day,why",
    [
        (MOCK_DAY.replace(day=2), "not a trading day"),
        (MOCK_DAY.replace(day=25), "special session"),
        (MOCK_DAY.replace(month=12), "does not cover"),
    ],
)
def test_days_without_a_report_are_recorded_with_the_reason(db, mock_calendar, day, why):
    assert why in plan(db, mock_calendar, day=day)
    (job,) = jobs(db)
    assert job.state == "SKIPPED" and why in job.last_error


def test_worker_does_not_start_before_the_window(db, mock_calendar):
    plan(db, mock_calendar)
    assert run_one(db, deps(Clock(ist(7, 0)), mock_calendar), "w1") == "idle"


# ---- leases, fencing, restarts ----------------------------------------------------------


def test_stale_worker_cannot_publish_after_its_lease_expired(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    plan(db, mock_calendar)
    with db() as s:
        lease_a = lease_next(s, "worker-A", ist(7, 40), lease_for=timedelta(minutes=5))
        s.commit()
    clock = Clock(ist(7, 50))  # A's lease expired; B takes over and finishes
    assert run_one(db, deps(clock, mock_calendar), "worker-B") == "done"
    with db() as s, pytest.raises(StaleLeaseError):
        complete(s, lease_a, {"from": "A"}, ist(7, 55))
    (job,) = jobs(db)
    assert job.state == "DONE" and job.lease_epoch == 2 and job.result["report_id"]
    parts = outbox(db, "report_part")
    assert len({p.part_no for p in parts}) == len(parts)  # one set, no duplicates


def test_worker_crash_then_restart_produces_exactly_one_report(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    plan(db, mock_calendar)
    with db() as s:  # worker leases then dies silently
        lease_next(s, "dead", ist(7, 31), lease_for=timedelta(minutes=5))
        s.commit()
    assert run_one(db, deps(Clock(ist(7, 40)), mock_calendar), "w2") == "done"
    assert run_one(db, deps(Clock(ist(7, 41)), mock_calendar), "w3") == "idle"
    with db() as s:
        assert len(s.execute(select(StoredReport)).scalars().all()) == 1


def test_cancelled_job_cannot_be_completed_by_its_worker(db, mock_calendar):
    plan(db, mock_calendar)
    with db() as s:
        lease = lease_next(s, "w1", ist(7, 40))
        s.commit()
    with db() as s:
        cancel(s, lease.job_id, "operator cancelled", ist(7, 41))
        s.commit()
    with db() as s, pytest.raises(StaleLeaseError):
        complete(s, lease, {}, ist(7, 42))


def test_database_failure_mid_write_leaves_nothing_half_done(db, mock_calendar, monkeypatch):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    plan(db, mock_calendar)
    import desk.jobs.worker as worker

    def boom(*a, **k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(worker, "enqueue_report", boom)
    with pytest.raises(RuntimeError):
        run_one(db, deps(Clock(ist(7, 40)), mock_calendar), "w1")
    (job,) = jobs(db)
    assert job.state == "LEASED"  # DONE was rolled back together with the writes
    assert outbox(db) == []
    monkeypatch.undo()
    assert run_one(db, deps(Clock(ist(7, 51)), mock_calendar), "w2") == "done"
    assert outbox(db, "report_part")


# ---- feed / review / budget / deadline failures -----------------------------------------


def _feed_down(day):
    raise ConnectionError("feed host unreachable at 10.0.0.7 token=abc123")


def test_feed_failure_retries_then_tells_users_plainly(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    plan(db, mock_calendar)
    clock = Clock(ist(7, 31))
    results = []
    for _ in range(3):
        results.append(run_one(db, deps(clock, mock_calendar, feed_for=_feed_down), "w1"))
        clock.now += timedelta(minutes=2)
    assert results == ["failed:feed:retry", "failed:feed:retry", "failed:feed:final"]
    (job,) = jobs(db)
    assert job.state == "FAILED" and "ConnectionError" in job.last_error  # operator sees it
    (notice,) = outbox(db, "failure_notice")
    assert FAILURE_REASONS["feed"] in notice.body and "koi ETA nahi" in notice.body
    assert "10.0.0.7" not in notice.body and "token" not in notice.body  # no internals
    assert outbox(db, "report_part") == []


def test_review_rejection_fails_at_once_without_retry(db, mock_calendar, monkeypatch):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    plan(db, mock_calendar)
    import desk.jobs.worker as worker

    def rejected(**kw):
        raise ReviewRejectedError("mock source in a non-mock report")

    monkeypatch.setattr(worker, "run_report", rejected)
    assert run_one(db, deps(Clock(ist(7, 31)), mock_calendar), "w1") == "failed:review:final"
    assert FAILURE_REASONS["review"] in outbox(db, "failure_notice")[0].body


def test_over_budget_result_is_discarded(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    plan(db, mock_calendar)
    clock = Clock(ist(7, 31))

    class SlowClock(Clock):
        def elapsed(self):
            self.ticks += 1000  # every reading jumps 1000 s: run looks 1000 s long
            return self.ticks

    slow = SlowClock(clock.now)
    assert run_one(db, deps(slow, mock_calendar), "w1") == "failed:budget:retry"
    assert outbox(db, "report_part") == []


def test_late_report_is_labelled_late(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    plan(db, mock_calendar)
    assert run_one(db, deps(Clock(ist(8, 58)), mock_calendar), "w1") == "done:late"
    first = min(outbox(db, "report_part"), key=lambda p: p.part_no)
    assert "LATE: ready 01 Oct 2026 08:58 IST, target 01 Oct 2026 08:45 IST" in first.body
    assert jobs(db)[0].result["late"] is True


def test_missing_the_hard_stop_fails_visibly_and_publishes_nothing(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    plan(db, mock_calendar)
    with db() as s:
        stuck = lease_next(s, "slow", ist(8, 50))
        s.commit()
    assert run_one(db, deps(Clock(ist(9, 20)), mock_calendar), "w2") == "idle"
    (job,) = jobs(db)
    assert job.state == "FAILED" and job.last_error == "missed hard stop"
    assert FAILURE_REASONS["deadline"] in outbox(db, "failure_notice")[0].body
    with db() as s, pytest.raises(StaleLeaseError):  # the slow worker can't publish late
        complete(s, stuck, {}, ist(9, 25))
    assert outbox(db, "report_part") == []


def test_no_report_on_holiday_and_no_notice_spam(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    plan(db, mock_calendar, day=MOCK_DAY.replace(day=2))
    assert run_one(db, deps(Clock(ist(7, 40, day=2)), mock_calendar), "w1") == "idle"
    assert outbox(db) == []


def test_feed_serving_old_files_gives_an_empty_honest_report_not_yesterdays(mock_calendar):
    """5 Oct report, but the feed only has 1 Oct files: every lens is UNAVAILABLE."""
    from desk.agents.model import MockModelAdapter

    report = run_report(
        calendar=mock_calendar,
        feed=feed("full_mock"),
        model=MockModelAdapter(),
        trading_date=MOCK_DAY.replace(day=5),
    )
    assert [r.lens.value for r in report.lenses] == [f"R{i:02d}" for i in range(1, 16)]
    assert sum(len(r.facts) for r in report.lenses) == 0
    assert report.top_gaps and report.review_status.value == "DEGRADED"


def test_job_state_helper_reports_current_state(db, mock_calendar):
    plan(db, mock_calendar)
    assert job_state(db, "MORNING_REPORT", MOCK_DAY).state == "PENDING"


def test_old_worker_cannot_finish_while_new_worker_holds_the_lease(db, mock_calendar):
    """Both workers see state LEASED; only the fencing epoch tells them apart."""
    plan(db, mock_calendar)
    with db() as s:
        old = lease_next(s, "old", ist(7, 31), lease_for=timedelta(minutes=5))
        s.commit()
    with db() as s:
        new = lease_next(s, "new", ist(7, 40))  # old lease expired; still mid-run
        s.commit()
    assert (old.epoch, new.epoch) == (1, 2)
    with db() as s, pytest.raises(StaleLeaseError):
        complete(s, old, {"from": "old"}, ist(7, 41))
    with db() as s:
        complete(s, new, {"from": "new"}, ist(7, 42))
        s.commit()
    assert jobs(db)[0].result == {"from": "new"}


def test_failed_addendum_does_not_send_a_second_no_report_notice(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    plan(db, mock_calendar, auction=True)
    clock = Clock(ist(7, 31))
    for _ in range(3):  # morning fails for good -> one notice
        run_one(db, deps(clock, mock_calendar, feed_for=_feed_down), "w1")
        clock.now += timedelta(minutes=2)
    clock.now = ist(9, 9)
    for _ in range(3):  # addendum fails too -> recorded, no extra notice
        run_one(db, deps(clock, mock_calendar, feed_for=_feed_down), "w1")
        clock.now += timedelta(seconds=70)  # retries are 1 minute apart
    assert {j.kind: j.state for j in jobs(db)} == {
        "AUCTION_ADDENDUM": "FAILED",
        "MORNING_REPORT": "FAILED",
    }
    assert len(outbox(db, "failure_notice")) == 1
