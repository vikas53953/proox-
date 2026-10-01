"""T01 Telegram TEST transport (owner-approved; WhatsApp stays the product channel).

Same onboarding, consent, outbox and scheduling rules as WhatsApp, plus Telegram's own:
no 24h window and no templates, no delivery receipts (SENT is final), deep-link invites
that are single use and expire within 24 h, blocked bot = opt-out, and the bot token
never reaching logs, reprs or commits."""

import logging
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from desk.config import GateBlockedError, Settings, TelegramSettings, load_settings
from desk.db.models import Invite, Outbox, Tenant, TransportCursor
from desk.jobs.scheduler import plan_day
from desk.jobs.worker import run_one
from desk.logsafe import install, redact
from desk.onboarding.invites import TELEGRAM_MAX_TTL, create_invite, telegram_deep_link
from desk.onboarding.messages import NEUTRAL
from desk.outbox.sender import send_batch
from desk.runner import run_cycle
from desk.transport.base import TELEGRAM
from desk.transport.telegram.client import (
    FakeTelegram,
    make_telegram_transport,
)
from desk.transport.telegram.poller import poll_once
from tests.conftest import MOCK_DAY
from tests.delivery_helpers import DRAFTS, Clock, deps, ist

BOT = FakeTelegram()
TG = TelegramSettings(bot_token=f"{BOT.bot_id}:0", bot_username=BOT.username)
ME, OTHER = 5550001, 5550002


@pytest.fixture
def tg():
    fake = FakeTelegram(bot_id=BOT.bot_id)
    transport, client = fake.transport(TG.bot_token)
    return fake, client, {"telegram": transport}


def invite(db, now=None, ttl=timedelta(hours=72)):
    with db() as s:
        inv, code = create_invite(
            s, business_phone_id=TG.bot_id, now=now or ist(7, 0), ttl=ttl, channel="telegram"
        )
        s.commit()
        return inv.id, code


def poll(db, client, at):
    return poll_once(db, client, TG, at)


def send(db, transports, at):
    return send_batch(db, transports, DRAFTS, at)


def tenants(db):
    with db() as s:
        return list(s.execute(select(Tenant)).scalars())


def out(db, **f):
    with db() as s:
        q = select(Outbox).order_by(Outbox.created_at, Outbox.part_no)
        for k, v in f.items():
            q = q.where(getattr(Outbox, k) == v)
        return list(s.execute(q).scalars())


# ---- deep-link invite -> one tenant -> welcome + separate opt-in -------------------------


def test_deep_link_start_binds_one_telegram_tenant(db, tg):
    fake, client, transports = tg
    _, code = invite(db)
    link = telegram_deep_link(BOT.username, code)
    assert link == f"https://t.me/{BOT.username}?start={code}" and len(code) <= 64
    fake.user_says(ME, f"/start {code}")
    assert poll(db, client, ist(7, 5)).handled == 1
    (t,) = tenants(db)
    assert (t.channel, t.business_phone_id, t.sender, t.opt_in_state) == (
        "telegram",
        TG.bot_id,
        str(ME),
        "asked",
    )
    send(db, transports, ist(7, 6))
    assert [c["method"] for c in fake.calls] == ["sendMessage", "sendMessage"]
    assert all(c["chat_id"] == str(ME) for c in fake.calls)
    assert {r.kind: r.state for r in out(db)} == {"welcome": "SENT", "opt_in": "SENT"}


def test_telegram_invite_is_single_use_and_capped_at_24h(db, tg):
    fake, client, transports = tg
    inv_id, code = invite(db, ttl=timedelta(days=7))
    with db() as s:
        inv = s.get(Invite, inv_id)
        assert inv.expires_at - inv.created_at == TELEGRAM_MAX_TTL and inv.channel == "telegram"
    fake.user_says(ME, f"/start {code}")
    fake.user_says(OTHER, f"/start {code}")  # forwarded link
    poll(db, client, ist(7, 5))
    assert [t.sender for t in tenants(db)] == [str(ME)]
    send(db, transports, ist(7, 6))
    assert out(db, recipient=str(OTHER))[0].body == NEUTRAL


def test_expired_telegram_link_gets_the_neutral_reply(db, tg):
    fake, client, _ = tg
    _, code = invite(db, now=ist(7, 0) - timedelta(days=2))
    fake.user_says(ME, f"/start {code}")
    poll(db, client, ist(7, 5))
    assert tenants(db) == [] and out(db)[0].kind == "neutral"


def test_telegram_and_whatsapp_identities_never_merge(db, tg, wa_settings):
    from desk.onboarding.service import handle_message
    from tests.whatsapp_helpers import inbound

    fake, client, _ = tg
    _, code = invite(db)
    fake.user_says(ME, f"/start {code}")
    poll(db, client, ist(7, 5))
    msg = inbound(wa_settings, str(ME), "Hi", "wamid.SAMEDIGITS")  # same digits on WhatsApp
    with db() as s:
        assert handle_message(s, wa_settings, msg, ist(7, 6)).handled_as == "not_bound"
        s.commit()
    assert len(tenants(db)) == 1


def test_groups_and_bots_are_ignored_and_the_cursor_advances(db, tg):
    fake, client, _ = tg
    fake.user_says(-100123, "/start x", chat_type="group")
    fake.user_says(999, "hello", is_bot=True)
    r = poll(db, client, ist(7, 5))
    assert (r.received, r.handled, r.skipped) == (2, 0, 2)
    with db() as s:
        assert s.get(TransportCursor, ("telegram", TG.bot_id)).last_update_id == r.offset
    assert poll(db, client, ist(7, 6)).received == 0  # confirmed updates are not re-read
    assert fake.confirmed_offset == r.offset + 1


def test_slash_commands_map_to_stop_and_start(db, tg):
    fake, client, transports = tg
    _, code = invite(db)
    for text in (f"/start {code}", "YES", "/stop"):
        fake.user_says(ME, text)
    poll(db, client, ist(7, 5))
    assert tenants(db)[0].opt_in_state == "stopped"
    fake.user_says(ME, "/start")
    poll(db, client, ist(7, 6))
    assert tenants(db)[0].opt_in_state == "asked"
    assert out(db)[-1].kind == "start"


# ---- delivery: no window, no templates, honest SENT -------------------------------------


def _opted_in_tenant(db, fake, client):
    _, code = invite(db)
    fake.user_says(ME, f"/start {code}")
    fake.user_says(ME, "YES")
    poll(db, client, ist(7, 5))


def test_morning_report_goes_out_with_no_window_and_stays_sent(db, tg, mock_calendar):
    fake, client, transports = tg
    _opted_in_tenant(db, fake, client)
    clock = Clock(ist(8, 0, day=1))
    with db() as s:
        plan_day(s, mock_calendar, MOCK_DAY, ist(7, 20))
        with db() as s2:  # last message 3 days ago: WhatsApp would have to wait
            t = s2.execute(select(Tenant)).scalar_one()
            t.last_inbound_at = ist(7, 0) - timedelta(days=3)
            s2.commit()
        s.commit()
    assert run_one(db, deps(clock, mock_calendar), "w1") == "done"
    send(db, transports, ist(8, 1))
    parts = out(db, kind="report_part")
    assert [p.state for p in parts] == ["SENT", "SENT", "SENT"]
    assert [c["method"] for c in fake.calls[-3:]] == ["sendMessage", "sendDocument", "sendPhoto"]
    assert all(c.get("multipart") for c in fake.calls[-2:])
    assert "part 2/3" in fake.calls[-2]["caption"]
    assert TELEGRAM.delivery_receipts is False and "SENT is final" in TELEGRAM.delivery_note


def test_rate_limit_respects_retry_after(db, tg):
    fake, client, transports = tg
    _opted_in_tenant(db, fake, client)
    fake.script = [
        {"error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 120}}
    ]
    send(db, transports, ist(7, 6))
    row = [r for r in out(db) if r.kind == "welcome"][0]
    assert row.state == "PENDING" and row.next_attempt_at >= ist(7, 8)


def test_blocking_the_bot_counts_as_opt_out(db, tg):
    fake, client, transports = tg
    _opted_in_tenant(db, fake, client)
    fake.script = [{"error_code": 403, "description": "Forbidden: bot was blocked by the user"}]
    send(db, transports, ist(7, 6))
    assert tenants(db)[0].opt_in_state == "stopped"
    assert any(r.state == "FAILED" and "blocked" in r.error for r in out(db))


def test_timeout_is_unknown_and_never_resent(db, tg):
    fake, client, transports = tg
    _opted_in_tenant(db, fake, client)
    fake.script = [httpx.ReadTimeout("no answer")]
    send(db, transports, ist(7, 6))
    n = len(fake.calls)
    send(db, transports, ist(9, 0))
    unknown = [r for r in out(db) if r.state == "UNKNOWN"]
    assert len(unknown) == 1 and "not resent" in unknown[0].error
    assert all(c["chat_id"] for c in fake.calls[n:])  # later sends are other rows only
    assert unknown[0].provider_message_id is None  # Telegram cannot reconcile it


def test_runner_cycle_end_to_end(db, tg, mock_calendar):
    fake, client, transports = tg
    _, code = invite(db)
    fake.user_says(ME, f"/start {code}")
    fake.user_says(ME, "YES")
    r = run_cycle(
        db,
        deps=deps(Clock(ist(8, 0)), mock_calendar),
        transports=transports,
        templates=DRAFTS,
        now=ist(8, 0),
        telegram=(client, TG),
    )
    assert r.poll.handled == 2 and r.work[0] == "done" and r.send.get("SENT", 0) >= 5


# ---- secrets ------------------------------------------------------------------------------


def test_token_never_in_repr_or_logs(caplog):
    token = "8123456789:" + "AAE" + "k" * 32  # token-shaped, built at runtime
    settings = load_settings({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_BOT_USERNAME": "x_bot"})
    assert token not in repr(settings) and settings.telegram.bot_id == "8123456789"
    install()
    fake = FakeTelegram(bot_id=8123456789)
    client = fake.client(token)
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("desk.test").warning(
            "calling %s", f"https://api.telegram.org/bot{token}/getMe"
        )
        logging.getLogger("httpx").warning(
            "HTTP Request: POST https://api.telegram.org/bot%s/x", token
        )
        client.get_me()
    assert token not in caplog.text and "[REDACTED]" in caplog.text
    assert redact(f"bot{token}/sendMessage") == "[REDACTED]/sendMessage"


def test_live_telegram_needs_flag_token_and_pinned_version():
    plain = Settings(mode="mock", database_url=None, model_adapter="mock", feed_adapter="fixture")
    transport, _ = make_telegram_transport(plain)  # default: in-memory fake
    assert transport.caps is TELEGRAM
    no_token = Settings(
        mode="mock",
        database_url=None,
        model_adapter="mock",
        feed_adapter="fixture",
        telegram=TelegramSettings(live=True),
    )
    with pytest.raises(GateBlockedError, match="TOKEN"):
        make_telegram_transport(no_token)
    with_token = Settings(
        mode="mock",
        database_url=None,
        model_adapter="mock",
        feed_adapter="fixture",
        telegram=TelegramSettings(bot_token=":".join(("1", "x")), live=True),
    )
    with pytest.raises(GateBlockedError, match="pin the Telegram Bot API version"):
        make_telegram_transport(with_token)


def test_stop_after_report_is_queued_cancels_it_on_telegram_too(db, tg, mock_calendar):
    """No 24h window on Telegram does NOT mean no consent check: a /stop committed before
    the sender decides a part cancels it. (Cutoff for a send already in flight: see
    test_stop_cutoff_is_the_per_message_send_decision.)"""
    fake, client, transports = tg
    _opted_in_tenant(db, fake, client)
    send(db, transports, ist(7, 6))  # welcome / opt-in replies out of the way
    with db() as s:
        plan_day(s, mock_calendar, MOCK_DAY, ist(7, 20))
        s.commit()
    assert run_one(db, deps(Clock(ist(8, 0)), mock_calendar), "w1") == "done"
    fake.user_says(ME, "/stop")
    poll(db, client, ist(8, 0))
    n = len(fake.calls)
    send(db, transports, ist(8, 1))
    assert {p.state for p in out(db, kind="report_part")} == {"CANCELLED"}
    assert [c["method"] for c in fake.calls[n:]] == ["sendMessage"]  # only the STOP reply


# ---- security review fixes (HIGH-1..3, MED-4..7) -----------------------------------------


def _report_queued(db, fake, client, transports, mock_calendar):
    _opted_in_tenant(db, fake, client)
    send(db, transports, ist(7, 6))  # welcome / opt-in replies out of the way
    with db() as s:
        plan_day(s, mock_calendar, MOCK_DAY, ist(7, 20))
        s.commit()
    assert run_one(db, deps(Clock(ist(8, 0)), mock_calendar), "w1") == "done"
    return len(fake.calls)


def _settings(**tg):
    return Settings(
        mode="mock",
        database_url=None,
        model_adapter="mock",
        feed_adapter="fixture",
        telegram=TelegramSettings(**tg),
    )


def test_fake_mode_is_refused_while_a_real_token_is_set():
    """HIGH-1: token present but not live -> no fake that could mark real rows SENT."""
    with pytest.raises(GateBlockedError, match="refusing the in-memory fake"):
        make_telegram_transport(_settings(bot_token=":".join(("8123456789", "x"))))
    transport, _ = make_telegram_transport(_settings())
    assert transport.endpoint == str(FakeTelegram().bot_id)


def test_a_transport_only_sends_its_own_bots_rows(db, tg):
    """HIGH-1/HIGH-2: rows of another bot (or of WhatsApp) are never claimed or touched."""
    fake, client, transports = tg
    _opted_in_tenant(db, fake, client)
    other_bot = FakeTelegram(bot_id=4242424242)
    other_transport, _ = other_bot.transport()
    stats = send(db, {"telegram": other_transport}, ist(7, 6))
    assert other_bot.calls == [] and stats.counts == {}
    assert {r.state for r in out(db)} == {"PENDING"} and all(r.attempts == 0 for r in out(db))
    send(db, transports, ist(7, 7))  # the right bot sends them
    assert {r.state for r in out(db)} == {"SENT"}


def test_logs_never_carry_the_token_via_exceptions_extra_or_stack(caplog, capsys):
    """HIGH-3: exc_info, exc_text, stack_info, extra fields and uncaught exceptions."""
    import sys
    import threading

    token = "8123456789:" + "AAE" + "q" * 32
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    install()
    log = logging.getLogger("desk.test")
    with caplog.at_level(logging.DEBUG):
        try:
            raise httpx.ConnectError(f"failed {url}")
        except httpx.ConnectError:
            log.exception("poll failed")
        log.warning("with extra", extra={"url": url, "attempt": 3}, stack_info=True)
        log.error("plain %s", token)
    assert token not in caplog.text and caplog.text.count("[REDACTED]") >= 2
    assert caplog.records[1].url == "https://api.telegram.org/[REDACTED]/getUpdates"
    for rec in caplog.records:
        assert rec.exc_info is None  # raw traceback (and its frames' locals) dropped
        assert token not in str(vars(rec))
    assert caplog.records[1].attempt == 3  # untouched extra fields keep their type
    raw = logging.makeLogRecord({"msg": f"direct {url}"})  # bypasses Logger.makeRecord
    assert token not in logging.Formatter().format(raw)
    try:
        raise RuntimeError(url)
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    t = threading.Thread(target=lambda: (_ for _ in ()).throw(RuntimeError(url)), name="tg")
    t.start()
    t.join()
    err = capsys.readouterr().err
    assert token not in err and err.count("[REDACTED]") >= 2


def test_rate_limit_holds_later_parts_and_keeps_order(db, tg, mock_calendar):
    """MED-4: a 429 on part 1 pauses the bot; parts 2-3 are released unspent and can
    never overtake part 1."""
    fake, client, transports = tg
    n = _report_queued(db, fake, client, transports, mock_calendar)
    fake.script = [
        {"error_code": 429, "description": "Too Many", "parameters": {"retry_after": 60}}
    ]
    stats = send(db, transports, ist(8, 1))
    parts = out(db, kind="report_part")
    assert len(fake.calls) == n + 1 and stats.counts.get("HELD") == 2
    assert [p.state for p in parts] == ["PENDING"] * 3
    assert [p.attempts for p in parts] == [1, 0, 0]
    send(db, transports, ist(8, 1) + timedelta(seconds=30))  # still cooling: nothing goes
    assert len(fake.calls) == n + 1
    send(db, transports, ist(8, 3))
    assert [c["method"] for c in fake.calls[n:]] == [
        "sendMessage",
        "sendMessage",
        "sendDocument",
        "sendPhoto",
    ]
    assert [p.state for p in out(db, kind="report_part")] == ["SENT"] * 3


def test_unclaimed_later_parts_wait_for_a_cooling_part_1(db, tg, mock_calendar):
    """MED-4: parts never claimed in the 429 batch (limit) still may not overtake part 1."""
    fake, client, transports = tg
    n = _report_queued(db, fake, client, transports, mock_calendar)
    fake.script = [
        {"error_code": 429, "description": "Too Many", "parameters": {"retry_after": 60}}
    ]
    send_batch(db, transports, DRAFTS, ist(8, 1), limit=1)  # only part 1 claimed -> 429
    send(db, transports, ist(8, 1) + timedelta(seconds=30))
    assert len(fake.calls) == n + 1  # parts 2-3 are due, but their recipient is cooling
    send(db, transports, ist(8, 3))
    assert [c["method"] for c in fake.calls[n + 1 :]] == [
        "sendMessage",
        "sendDocument",
        "sendPhoto",
    ]


def test_transient_poll_error_does_not_stop_the_cycle(db, tg, mock_calendar):
    """MED-5: a 502 / network error on getUpdates skips that poll only."""
    fake, client, transports = tg
    _opted_in_tenant(db, fake, client)
    for step in (502, httpx.ReadTimeout("slow"), {"error_code": 429, "description": "x"}):
        fake.poll_script = [step]
        r = run_cycle(
            db,
            deps=deps(Clock(ist(8, 0)), mock_calendar),
            transports=transports,
            templates=DRAFTS,
            now=ist(8, 0),
            telegram=(client, TG),
        )
        assert str(r.poll).startswith("poll skipped") and "bot" not in str(r.poll)
    assert {r.state for r in out(db, kind="welcome")} == {"SENT"}


@pytest.mark.parametrize("code", [401, 404, 409])
def test_auth_and_conflict_poll_errors_are_fatal(db, tg, mock_calendar, code):
    from desk.transport.telegram.client import TelegramFatalError

    fake, client, transports = tg
    fake.poll_script = [{"error_code": code, "description": "nope"}]
    with pytest.raises(TelegramFatalError, match=str(code)):
        run_cycle(
            db,
            deps=deps(Clock(ist(8, 0)), mock_calendar),
            transports=transports,
            templates=DRAFTS,
            now=ist(8, 0),
            telegram=(client, TG),
        )


def test_serve_loop_backs_off_boundedly_and_logs_no_secret():
    """MED-5: errors never end the loop; waits double up to a cap and reset on success."""
    from desk.runner import BACKOFF_MAX_S, CycleReport, serve_loop
    from desk.transport.telegram.client import TelegramFatalError

    token = "8123456789:" + "AAE" + "z" * 32
    results = [RuntimeError(f"db down bot{token}")] * 8 + [CycleReport(), RuntimeError("x")]
    lines, waits = [], []

    def cycle():
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    serve_loop(cycle, log=lines.append, sleep=waits.append, max_cycles=10)
    assert waits == [5, 10, 20, 40, 80, 160, 300, 300, 1, 5]
    assert max(waits) == BACKOFF_MAX_S and not any(token in line for line in lines)

    def fatal():
        raise TelegramFatalError("getUpdates: 409 another poller")

    with pytest.raises(TelegramFatalError):
        serve_loop(fatal, log=lines.append, sleep=waits.append, max_cycles=3)


def test_dedupe_survives_update_id_reset_and_stale_cursor_restarts(db, tg):
    """MED-6: same (chat, message_id) under a new update_id is handled once; a cursor
    idle longer than STALE_CURSOR is not trusted (Telegram re-randomises update_ids)."""
    from desk.transport.telegram.poller import STALE_CURSOR

    fake, client, _ = tg
    _, code = invite(db)
    fake.user_says(ME, f"/start {code}", update_id=5000, message_id=7)
    assert poll(db, client, ist(7, 5)).handled == 1
    assert poll(db, client, ist(7, 6)).received == 0 and fake.confirmed_offset == 5001
    fake.user_says(ME, f"/start {code}", update_id=42, message_id=7)  # ids re-randomised
    later = ist(7, 6) + STALE_CURSOR + timedelta(hours=1)
    # Re-randomised ids only appear after 7 idle days; switching to offset 0 BEFORE that
    # is what keeps a high stale offset from confirming (= dropping) a new low id.
    assert STALE_CURSOR < timedelta(days=7)
    r = poll(db, client, later)  # stale cursor: offset 0 re-reads all unconfirmed
    assert r.received == 1 and r.handled == 1
    assert len(tenants(db)) == 1 and len(out(db, kind="welcome")) == 1  # no second welcome
    fake.user_says(ME, "what about nifty?", update_id=44, message_id=9)
    poll(db, client, later + timedelta(seconds=10))
    fake.user_says(ME, "what about nifty?", update_id=45, message_id=9)  # same message again
    poll(db, client, later + timedelta(seconds=20))
    assert len(out(db, kind="question_pending")) == 1  # one reply per Telegram message
    fake.user_says(ME, "YES", update_id=46, message_id=8)
    assert poll(db, client, later + timedelta(minutes=1)).handled == 1
    with db() as s:
        assert s.get(TransportCursor, ("telegram", TG.bot_id)).last_update_id == 46
    assert tenants(db)[0].opt_in_state == "yes"


def test_stop_cutoff_is_the_per_message_send_decision(db, tg, mock_calendar):
    """MED-7: STOP takes effect for every message whose send decision is made after the
    STOP is committed. A part already handed to Telegram when STOP arrives completes;
    every later part is cancelled."""
    fake, client, transports = tg
    n = _report_queued(db, fake, client, transports, mock_calendar)
    inner = transports["telegram"]
    stopped = []

    class StopDuringFirstSend:
        caps, endpoint = inner.caps, inner.endpoint

        def send_text(self, *a):
            if not stopped:  # the person sends /stop while part 1 is in flight
                fake.user_says(ME, "/stop")
                stopped.append(poll(db, client, ist(8, 1)).handled)
            return inner.send_text(*a)

        def send_media(self, *a):
            return inner.send_media(*a)

        def send_template(self, *a):
            return inner.send_template(*a)

    send(db, {"telegram": StopDuringFirstSend()}, ist(8, 1))
    assert stopped == [1]
    assert [p.state for p in out(db, kind="report_part")] == ["SENT", "CANCELLED", "CANCELLED"]
    assert [c["method"] for c in fake.calls[n:]] == ["sendMessage"]  # part 1 only
    send(db, transports, ist(8, 2))
    assert [c["method"] for c in fake.calls[n:]] == ["sendMessage", "sendMessage"]  # + STOP ack
