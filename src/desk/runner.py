"""One loop for the owner's PC test: poll Telegram -> plan today's jobs -> run the
worker until idle -> send the outbox. Each step is the same code the tests exercise."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from desk.jobs.scheduler import plan_day
from desk.jobs.worker import Deps, run_one
from desk.outbox.policy import ist_today
from desk.outbox.sender import send_batch
from desk.transport.telegram.poller import poll_once
from desk.transport.whatsapp.templates import TemplateRegistry

MAX_JOBS_PER_CYCLE = 5


@dataclass
class CycleReport:
    poll: object = None
    plan: str = ""
    work: list[str] = field(default_factory=list)
    send: dict[str, int] = field(default_factory=dict)


def run_cycle(
    factory: sessionmaker[Session],
    *,
    deps: Deps,
    transports: dict,
    templates: TemplateRegistry,
    now: datetime,
    telegram=None,
    auction_enabled: bool = False,
    poll_timeout_s: int = 0,
) -> CycleReport:
    report = CycleReport()
    if telegram is not None:  # (client, TelegramSettings)
        client, tg = telegram
        report.poll = poll_once(factory, client, tg, now, timeout_s=poll_timeout_s)
    with factory() as s:
        report.plan = plan_day(s, deps.calendar, ist_today(now), now, auction_enabled)
        s.commit()
    for _ in range(MAX_JOBS_PER_CYCLE):
        result = run_one(factory, deps, "pc-runner")
        report.work.append(result)
        if result == "idle":
            break
    report.send = send_batch(factory, transports, templates, now).counts
    return report
