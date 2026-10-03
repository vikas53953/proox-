"""M8T review fixes (2026-10-03): poll backoff honours retry_after, inbound trouble never
stops outbound sending, and migration 0004 refuses to downgrade over Telegram rows."""

import os
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from desk.runner import BACKOFF_MAX_S, BACKOFF_START_S, PollBackoff, run_cycle
from desk.transport.telegram.client import FakeTelegram, TelegramTransientError
from tests.constitution.test_t01_telegram import BOT, TG, _opted_in_tenant, out
from tests.delivery_helpers import DRAFTS, Clock, deps, ist


@pytest.fixture
def tg():  # same as the T01 fixture
    fake = FakeTelegram(bot_id=BOT.bot_id)
    transport, client = fake.transport(TG.bot_token)
    return fake, client, {"telegram": transport}


def _cycle(db, tg, mock_calendar, at, backoff):
    fake, client, transports = tg
    return run_cycle(
        db,
        deps=deps(Clock(at), mock_calendar),
        transports=transports,
        templates=DRAFTS,
        now=at,
        telegram=(client, TG),
        poll_backoff=backoff,
    )


def test_429_on_get_updates_waits_for_telegrams_retry_after(db, tg, mock_calendar):
    fake, _, _ = tg
    backoff = PollBackoff()
    fake.poll_script = [{"error_code": 429, "description": "x", "parameters": {"retry_after": 120}}]
    r = _cycle(db, tg, mock_calendar, ist(8, 0), backoff)
    assert str(r.poll).startswith("poll skipped")
    assert backoff.next_poll_at == ist(8, 0) + timedelta(seconds=120)
    n = len(fake.calls)
    r = _cycle(db, tg, mock_calendar, ist(8, 1), backoff)  # 60 s later: still waiting
    assert "backing off" in str(r.poll)
    assert not [c for c in fake.calls[n:] if c["method"] == "getUpdates"]
    r = _cycle(db, tg, mock_calendar, ist(8, 2), backoff)  # 120 s later: polls again
    assert not str(r.poll).startswith("poll skipped")
    assert backoff.failures == 0 and backoff.next_poll_at is None


def test_repeated_poll_failures_back_off_exponentially_and_are_capped():
    b, t = PollBackoff(), ist(8, 0)
    waits = []
    for _ in range(10):
        b.failed(t)
        waits.append((b.next_poll_at - t).total_seconds())
    assert waits[0] == BACKOFF_START_S and waits[1] == 2 * BACKOFF_START_S
    assert max(waits) == BACKOFF_MAX_S


def test_transient_error_carries_retry_after_only_for_429():
    from desk.transport.telegram.client import _api_error

    class R:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    e = _api_error("getUpdates", R(429, {"error_code": 429, "parameters": {"retry_after": 7}}))
    assert isinstance(e, TelegramTransientError) and e.retry_after_s == 7
    assert _api_error("getUpdates", R(502, {})).retry_after_s is None


def test_a_bad_update_does_not_stop_outbound_sending(db, tg, mock_calendar, monkeypatch):
    fake, client, transports = tg
    _opted_in_tenant(db, fake, client)
    welcome_rows = out(db, kind="welcome")
    assert welcome_rows

    def boom(*a, **k):
        raise ValueError("bad update")

    monkeypatch.setattr("desk.runner.poll_once", boom)
    backoff = PollBackoff()
    r = _cycle(db, tg, mock_calendar, ist(8, 0), backoff)
    assert str(r.poll).startswith("poll failed: ValueError")
    assert r.plan  # planning, work and sending still ran
    assert isinstance(r.send, dict)
    assert backoff.failures == 1


def test_downgrade_of_0004_refuses_while_telegram_rows_exist():
    from alembic import command
    from alembic.config import Config

    admin_url = os.environ.get("DESK_TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("needs PostgreSQL: set DESK_TEST_DATABASE_URL")
    name = f"desk_dg_{uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = admin_url.rsplit("/", 1)[0] + f"/{name}"
    engine = create_engine(url)
    try:
        repo = Path(__file__).resolve().parents[1]
        cfg = Config(str(repo / "alembic.ini"))
        cfg.set_main_option("script_location", str(repo / "alembic"))
        cfg.attributes["url"] = url
        command.upgrade(cfg, "0004")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO invites (id, token_hash, channel, business_phone_id,"
                    " created_at, expires_at, state)"
                    " VALUES (:id, :h, 'telegram', '1', now(), now(), 'open')"
                ),
                {"id": uuid.uuid4(), "h": "0" * 64},
            )
        with pytest.raises(RuntimeError, match="non-WhatsApp rows"):
            command.downgrade(cfg, "0003")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM invites"))
        command.downgrade(cfg, "0003")  # clean data: downgrade works
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
