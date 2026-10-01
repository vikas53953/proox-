"""D07 UAT driver: three simulated days of the WhatsApp desk, end to end, MOCK only.

Real code paths: invite -> webhook processing -> onboarding -> scheduler -> worker ->
PDF/PNG rendering -> outbox policy -> sender -> receipts -> corrections. The only fake is
Meta's server (in-memory FakeGraph): nothing is sent anywhere. A fresh throwaway
database is created and dropped. Simulated clock; mock numbers; MOCK market data.

    DESK_TEST_DATABASE_URL=postgresql+psycopg://USER@HOST:PORT/postgres \
    PYTHONPATH=src python uat/mock_days.py OUT_DIR

Writes OUT_DIR/transcript.json and OUT_DIR/media/*.
"""

import itertools
import json
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text

from desk.agents.model import MockModelAdapter
from desk.config import WhatsAppSettings
from desk.core.lens import LensId
from desk.db.models import Job, Media, Outbox
from desk.db.session import make_engine, make_session_factory
from desk.feedback import issue_correction
from desk.feeds.fixture import FixtureFeed
from desk.jobs.scheduler import plan_day
from desk.jobs.worker import Deps, run_one
from desk.market_calendar import IST, TradingCalendar
from desk.onboarding.invites import create_invite
from desk.outbox.sender import send_batch
from desk.transport.whatsapp.client import FakeGraph
from desk.transport.whatsapp.payload import InboundMessage, StatusUpdate
from desk.transport.whatsapp.templates import TemplateRegistry
from desk.transport.whatsapp.webhook import _process

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "fixtures"
VIKAS, STRANGER = "919800000001", "919800000099"  # MOCK numbers


def at(day: int, hh: int, mm: int) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=IST).astimezone(UTC)


class Day:
    def __init__(self, out: Path, factory, wa, graph):
        self.out, self.factory, self.wa, self.graph = out, factory, wa, graph
        self.events: list[dict] = []
        self.templates = TemplateRegistry.load(REPO / "config" / "whatsapp_templates.json")
        self._ids = itertools.count(1)
        self._seen_requests = 0
        (out / "media").mkdir(parents=True, exist_ok=True)

    def note(self, now, textline, thread="vikas"):
        self.events.append(
            {"t": now.isoformat(), "dir": "system", "thread": thread, "text": textline}
        )

    def say(self, now, sender, body):
        thread = "vikas" if sender == VIKAS else "stranger"
        self.events.append({"t": now.isoformat(), "dir": "in", "thread": thread, "text": body})
        msg = InboundMessage(
            f"wamid.UAT{next(self._ids)}",
            self.wa.waba_id,
            self.wa.phone_number_id,
            sender,
            now,
            "text",
            body,
        )
        _process(
            SimpleNamespace(
                settings=SimpleNamespace(whatsapp=self.wa), session_factory=self.factory
            ),
            [msg],
            now,
        )
        self.flush(now + timedelta(seconds=20))

    def flush(self, now):
        """Run the sender once and record what actually went out (or why not)."""
        before = now - timedelta(seconds=1)
        send_batch(self.factory, self.graph.client(), self.templates, now)
        new = self.graph.requests[self._seen_requests :]
        self._seen_requests = len(self.graph.requests)
        with self.factory() as s:
            for req in new:
                row = s.get(Outbox, uuid.UUID(req["biz_opaque_callback_data"]))
                ev = {
                    "t": now.isoformat(),
                    "dir": "out",
                    "thread": "vikas" if row.recipient == VIKAS else "stranger",
                    "kind": row.kind,
                    "text": row.body,
                    "state": row.state,
                    "row": str(row.id),
                }
                if row.media_id:
                    media = s.get(Media, row.media_id)
                    path = self.out / "media" / media.filename
                    path.write_bytes(media.content)
                    ev["media"] = {
                        "kind": media.kind,
                        "file": f"media/{media.filename}",
                        "mime": media.mime,
                    }
                self.events.append(ev)
            held = (
                s.execute(
                    select(Outbox).where(
                        Outbox.last_status_at >= before,
                        Outbox.state.in_(("WAITING_WINDOW", "CANCELLED")),
                    )
                )
                .scalars()
                .all()
            )
            for row in held:
                why = row.wait_reason or row.error
                self.note(
                    now,
                    f"NOT SENT ({row.state}) {row.kind}: {why}",
                    "vikas" if row.recipient == VIKAS else "stranger",
                )
        self.receipts(now)

    def receipts(self, now):
        """Meta's delivery receipts arrive: delivered, then read."""
        with self.factory() as s:
            accepted = s.execute(select(Outbox).where(Outbox.state == "ACCEPTED")).scalars()
            ups = [(r.provider_message_id, r.recipient, str(r.id)) for r in accepted]
        for status in ("delivered", "read"):
            sts = [StatusUpdate(pid, status, now, rcpt, cb, None, None) for pid, rcpt, cb in ups]
            _process(
                SimpleNamespace(
                    settings=SimpleNamespace(whatsapp=self.wa), session_factory=self.factory
                ),
                [],
                now,
                sts,
            )
        with self.factory() as s:
            for ev in self.events:
                if ev.get("row"):
                    ev["state"] = s.get(Outbox, uuid.UUID(ev["row"])).state


def main(out: Path) -> None:
    admin_url = os.environ["DESK_TEST_DATABASE_URL"]
    name = f"desk_uat_{uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = admin_url.rsplit("/", 1)[0] + f"/{name}"
    try:
        cfg = Config(str(REPO / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO / "alembic"))
        cfg.attributes["url"] = url
        command.upgrade(cfg, "head")
        engine = make_engine(url)
        run(out, make_session_factory(engine))
        engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def run(out: Path, factory) -> None:
    wa = WhatsAppSettings(
        phone_number_id="100200300",
        waba_id="900800700",
        app_secret=secrets.token_hex(32),
        verify_token=secrets.token_hex(16),
    )
    calendar = TradingCalendar.load(FIXTURES / "calendar" / "NSE-CM-holidays-2026-v1.json")
    d = Day(out, factory, wa, FakeGraph())
    good = lambda day: FixtureFeed(FIXTURES / "market", day, "auction_mock")  # noqa: E731

    def feed_down(day):
        raise ConnectionError("MOCK: feed host unreachable")

    def work(now, feed_for):
        clock = SimpleNamespace(now=now)
        deps = Deps(
            calendar=calendar,
            feed_for=feed_for,
            model=MockModelAdapter(),
            clock=lambda: clock.now,
            elapsed=lambda: 0.0,
        )
        return run_one(factory, deps, "uat-worker")

    def plan(now, day):
        with factory() as s:
            result = plan_day(s, calendar, day, now, auction_enabled=True)
            s.commit()
        return result

    # ---- Day 1: Thu 1 Oct 2026, trading day ------------------------------------------------
    now = at(1, 7, 0)
    with factory() as s:
        _, code = create_invite(
            s, business_phone_id=wa.phone_number_id, now=now, ttl=timedelta(hours=72)
        )
        s.commit()
    d.note(now, "Operator creates an invite (code shown once, only its hash is stored).")
    d.say(at(1, 7, 5), VIKAS, f"Hi {code}")
    d.say(at(1, 7, 6), VIKAS, "YES")
    d.say(at(1, 7, 10), VIKAS, "Aaj ke main catalysts?")
    d.say(at(1, 7, 15), STRANGER, "Hi")
    d.say(at(1, 7, 16), STRANGER, "Hello??")
    d.note(
        at(1, 7, 20),
        f"Scheduler: {plan(at(1, 7, 20), at(1, 7, 20).astimezone(IST).date())}"
        " (morning 07:30-08:45 target, 09:15 hard stop; auction addendum 09:12).",
    )
    d.note(at(1, 8, 0), f"Worker: {work(at(1, 8, 0), good)}")
    d.flush(at(1, 8, 1))
    d.note(at(1, 9, 10), f"Worker: {work(at(1, 9, 10), good)}")
    d.flush(at(1, 9, 11))
    d.say(at(1, 9, 20), VIKAS, "TEXT")
    d.say(at(1, 9, 30), VIKAS, "useful")
    d.say(at(1, 9, 31), VIKAS, "Feedback: R13 Greeks samajh aaye, R08 short rakho")
    with factory() as s:
        issue_correction(
            s,
            report_id="MOCK-2026-10-01-MORNING",
            from_version=1,
            lens=LensId.R04,
            previous_claim="Banks return +0.82%",
            corrected_claim="Banks return +0.62%",
            source_url="https://example.invalid/mock/sectors-revised",
            source_time=at(1, 9, 45),
            scenario_impact="none: base/up/down paths unchanged",
            reason="MOCK provider revised sector close",
            now=at(1, 10, 0),
        )
        s.commit()
    d.note(at(1, 10, 0), "Operator issues a verified correction to R04 (MOCK).")
    d.flush(at(1, 10, 1))

    # ---- Day 2: Fri 2 Oct 2026, Gandhi Jayanti (NSE holiday) --------------------------------
    d.note(at(2, 7, 20), f"Scheduler: {plan(at(2, 7, 20), at(2, 7, 20).astimezone(IST).date())}")
    d.note(at(2, 8, 0), f"Worker: {work(at(2, 8, 0), good)} (nothing to do, nothing sent)")

    # ---- Day 3: Mon 5 Oct 2026, feed down; >24h since Vikas last wrote ----------------------
    d.note(at(5, 7, 20), f"Scheduler: {plan(at(5, 7, 20), at(5, 7, 20).astimezone(IST).date())}")
    for i, hhmm in enumerate(((7, 31), (7, 33), (7, 35))):
        d.note(at(5, *hhmm), f"Worker try {i + 1}: {work(at(5, *hhmm), feed_down)}")
    d.flush(at(5, 7, 36))
    d.say(at(5, 8, 30), VIKAS, "Aaj report nahi aaya?")
    for i, hhmm in enumerate(((9, 9), (9, 11), (9, 13))):
        d.note(
            at(5, *hhmm),
            f"Worker addendum try {i + 1}: {work(at(5, *hhmm), feed_down)}"
            " (optional update: no second notice to the customer)",
        )
    d.flush(at(5, 9, 14))
    d.say(at(5, 9, 30), VIKAS, "STOP")
    with factory() as s:
        jobs = s.execute(select(Job).order_by(Job.trading_date, Job.kind)).scalars().all()
        job_lines = [
            f"{j.trading_date} {j.kind}: {j.state}" + (f" ({j.last_error})" if j.last_error else "")
            for j in jobs
        ]
    (out / "transcript.json").write_text(
        json.dumps(
            {
                "generated": "MOCK UAT run",
                "events": d.events,
                "jobs": job_lines,
                "media_note": "PDF/PNG exactly as delivered to WhatsApp (MOCK)",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
