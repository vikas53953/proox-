"""Calendar service: writes one unique dated job per report kind per day.

Times (IST): morning report may start 07:30, target 08:45, hard stop 09:15 (market
open — after that a "pre-market" report is no longer pre-market). Optional auction
addendum: start 09:08, target 09:12, hard stop 09:15. Days with no report still get a
SKIPPED row with the reason, so "nothing happened" is visible, never silent.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from desk.db.models import Job
from desk.market_calendar import CalendarNotCoveredError, TradingCalendar, ist_to_utc


@dataclass(frozen=True)
class JobSpec:
    kind: str
    not_before: time
    deadline: time
    hard_stop: time
    budget_seconds: int


MORNING = JobSpec("MORNING_REPORT", time(7, 30), time(8, 45), time(9, 15), 900)
AUCTION = JobSpec("AUCTION_ADDENDUM", time(9, 8), time(9, 12), time(9, 15), 120)


def _insert(
    session: Session, spec: JobSpec, day: date, now: datetime, state: str, reason: str | None = None
) -> None:
    session.execute(
        pg_insert(Job)
        .values(
            id=uuid.uuid4(),
            kind=spec.kind,
            trading_date=day,
            tenant_id=None,
            not_before=ist_to_utc(day, spec.not_before),
            deadline_at=ist_to_utc(day, spec.deadline),
            hard_stop_at=ist_to_utc(day, spec.hard_stop),
            state=state,
            lease_epoch=0,
            attempts=0,
            max_attempts=3,
            budget_seconds=spec.budget_seconds,
            last_error=reason,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(constraint="job_once_per_day")
    )


def plan_day(
    session: Session,
    calendar: TradingCalendar,
    day: date,
    now: datetime,
    auction_enabled: bool = False,
) -> str:
    specs = [MORNING] + ([AUCTION] if auction_enabled else [])
    try:
        trading = calendar.is_trading_day(day)
    except CalendarNotCoveredError:
        reason = f"calendar {calendar.version} does not cover {day}"
        trading = None
    else:
        reason = None
        if not trading:
            reason = f"not a trading day per calendar {calendar.version}"
        elif calendar.is_special_session(day):
            reason = "special session: no 08:45 report (see BACKLOG B01)"
    for spec in specs:
        _insert(session, spec, day, now, "SKIPPED" if reason else "PENDING", reason)
    return reason or "planned"
