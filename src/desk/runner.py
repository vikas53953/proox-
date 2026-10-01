"""One loop for the owner's PC test: poll Telegram -> plan today's jobs -> run the
worker until idle -> send the outbox. Each step is the same code the tests exercise.

Failure handling (MED-5): a transient Telegram poll error skips the poll for this cycle
only (planning, work and sending still run); a fatal one (token rejected, 409 another
poller/webhook) stops the loop. Any other error in a cycle is logged without secrets and
retried with bounded exponential backoff."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from desk.jobs.scheduler import plan_day
from desk.jobs.worker import Deps, run_one
from desk.logsafe import redact
from desk.outbox.policy import ist_today
from desk.outbox.sender import send_batch
from desk.transport.telegram.client import TelegramFatalError, TelegramTransientError
from desk.transport.telegram.poller import poll_once
from desk.transport.whatsapp.templates import TemplateRegistry

MAX_JOBS_PER_CYCLE = 5
BACKOFF_START_S, BACKOFF_MAX_S = 5, 300


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
        try:
            report.poll = poll_once(factory, client, tg, now, timeout_s=poll_timeout_s)
        except TelegramTransientError as exc:
            report.poll = f"poll skipped: {exc}"
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


def describe(exc: BaseException) -> str:
    """One safe line for the console: exception type + redacted, shortened text."""
    return redact(f"{type(exc).__name__}: {exc}")[:200]


def serve_loop(
    cycle: Callable[[], CycleReport],
    *,
    log: Callable[[str], None],
    sleep: Callable[[float], None],
    pause_s: float = 1,
    max_cycles: int | None = None,
) -> None:
    """Run cycles until max_cycles (None = forever). Fatal Telegram errors propagate."""
    failures, n = 0, 0
    while max_cycles is None or n < max_cycles:
        n += 1
        try:
            report = cycle()
        except TelegramFatalError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad cycle must not end the desk
            failures += 1
            wait = min(BACKOFF_START_S * 2 ** (failures - 1), BACKOFF_MAX_S)
            log(f"cycle failed ({describe(exc)}); retrying in {wait}s")
            sleep(wait)
            continue
        failures = 0
        log(f"poll={report.poll} plan={report.plan} work={report.work} send={report.send}")
        sleep(pause_s)
