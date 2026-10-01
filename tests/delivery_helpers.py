"""Shared builders for delivery / job tests (simulated clock, tenants, worker deps)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from desk.agents.model import MockModelAdapter
from desk.db.models import Tenant
from desk.jobs.worker import Deps
from desk.market_calendar import IST
from desk.transport.whatsapp.templates import Template, TemplateRegistry
from tests.conftest import feed

PHONE_ID = "100200300"
DAY_START = datetime(2026, 10, 1, 0, 0, tzinfo=IST)


def ist(hh: int, mm: int, day: int = 1) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=IST).astimezone(UTC)


@dataclass
class Clock:
    now: datetime
    ticks: float = 0.0

    def __call__(self) -> datetime:
        return self.now

    def elapsed(self) -> float:
        return self.ticks


def add_tenant(db, sender: str, opt_in: str = "yes", last_inbound: datetime | None = None):
    with db() as s:
        t = Tenant(
            id=uuid.uuid4(),
            business_phone_id=PHONE_ID,
            sender=sender,
            opt_in_state=opt_in,
            state="pending",
            created_at=ist(7, 0),
            last_inbound_at=last_inbound,
        )
        s.add(t)
        s.commit()
        return t.id


def deps(clock: Clock, mock_calendar, feed_for=None, model=None) -> Deps:
    return Deps(
        calendar=mock_calendar,
        feed_for=feed_for or (lambda day: feed("full_mock")),
        model=model or MockModelAdapter(),
        clock=clock,
        elapsed=clock.elapsed,
    )


def registry(status: str | None) -> TemplateRegistry:
    if status is None:
        return TemplateRegistry([])
    return TemplateRegistry([Template("report_ready_v1", "en", "UTILITY", status, "report_ready")])


DRAFTS = registry("DRAFT")
ONE_HOUR = timedelta(hours=1)
