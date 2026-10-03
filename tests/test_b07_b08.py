"""B07: optional Telegram chat-id allowlist (TELEGRAM_ALLOWED_CHAT_IDS, unset = no change).
B08: fake WhatsApp in `serve` refuses a real access token and never claims a real phone id's
rows."""

import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from desk.config import GateBlockedError, SettingsError, TelegramSettings, load_settings
from desk.db.models import Inbound, Invite, Outbox, Tenant
from desk.jobs.scheduler import plan_day
from desk.jobs.worker import run_one
from desk.onboarding.invites import create_invite
from desk.onboarding.messages import NEUTRAL
from desk.outbox.sender import send_batch
from desk.transport.telegram.client import FakeTelegram, make_telegram_transport
from desk.transport.telegram.poller import poll_once
from desk.transport.whatsapp.adapter import WhatsAppTransport
from desk.transport.whatsapp.client import (
    MOCK_PHONE_NUMBER_ID,
    FakeGraph,
    make_client,
    make_send_client,
)
from tests.conftest import MOCK_DAY
from tests.delivery_helpers import DRAFTS, Clock, deps, ist

BOT = FakeTelegram()
ME, OTHER = 5550001, 5550002
OPEN = TelegramSettings(bot_token=f"{BOT.bot_id}:0", bot_username=BOT.username)
LOCKED = replace(OPEN, allowed_chat_ids=frozenset({str(ME)}))


def setup(tg: TelegramSettings, fake: FakeTelegram | None = None):
    fake = fake or FakeTelegram(bot_id=BOT.bot_id)  # same fake = same update-id sequence
    transport, client = fake.transport(tg.bot_token)
    transport.allowed_recipients = tg.allowed_chat_ids
    return fake, client, {"telegram": transport}


def invite(db):
    with db() as s:
        inv, code = create_invite(
            s,
            business_phone_id=OPEN.bot_id,
            now=ist(7, 0),
            ttl=timedelta(hours=24),
            channel="telegram",
        )
        s.commit()
        return inv.id, code


def all_rows(db, model):
    with db() as s:
        return list(s.execute(select(model)).scalars())


# ---- B07: config ---------------------------------------------------------------------------


def test_allowlist_unset_or_empty_means_no_restriction():
    for env in ({}, {"TELEGRAM_ALLOWED_CHAT_IDS": ""}, {"TELEGRAM_ALLOWED_CHAT_IDS": "  "}):
        tg = load_settings(env).telegram
        assert tg.allowed_chat_ids is None and tg.allows("123")


def test_allowlist_parses_integers():
    tg = load_settings({"TELEGRAM_ALLOWED_CHAT_IDS": " 111, 222 ,-5,"}).telegram
    assert tg.allowed_chat_ids == frozenset({"111", "222", "-5"})
    assert tg.allows("111") and not tg.allows("333")


@pytest.mark.parametrize("raw", ["abc", "111,x", "1.5", ",", "111;222", "0x10"])
def test_invalid_allowlist_refuses_to_start(raw):
    with pytest.raises(SettingsError, match="TELEGRAM_ALLOWED_CHAT_IDS"):
        load_settings({"TELEGRAM_ALLOWED_CHAT_IDS": raw})


def test_cli_refuses_to_start_on_invalid_allowlist(monkeypatch, capsys):
    from desk.__main__ import main

    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "owner")
    monkeypatch.setattr(sys, "argv", ["desk", "serve", "--once"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert "refusing to start" in str(exc.value.code)
    assert "TELEGRAM_ALLOWED_CHAT_IDS" in str(exc.value.code)


def test_make_telegram_transport_carries_the_allowlist():
    s = load_settings({"TELEGRAM_ALLOWED_CHAT_IDS": "42"})
    transport, _ = make_telegram_transport(s)
    assert transport.allowed_recipients == frozenset({"42"})
    assert make_telegram_transport(load_settings({}))[0].allowed_recipients is None


def test_send_policy_cancels_tenant_rows_to_chats_not_on_the_list():
    from desk.outbox.policy import Action, decide

    def row(to, tenant_id):
        return Outbox(
            channel="telegram", recipient=to, tenant_id=tenant_id, kind="question_pending"
        )

    tid, now, locked = uuid.uuid4(), ist(8, 0), frozenset({str(ME)})
    blocked = decide(None, row(str(OTHER), tid), None, now, DRAFTS, locked)
    assert blocked.action is Action.CANCEL and "TELEGRAM_ALLOWED_CHAT_IDS" in blocked.reason
    assert decide(None, row(str(ME), tid), None, now, DRAFTS, locked).action is Action.SEND_TEXT
    # untenanted neutral reply still goes out; no list = today's behaviour
    assert decide(None, row(str(OTHER), None), None, now, DRAFTS, locked).action is Action.SEND_TEXT
    assert decide(None, row(str(OTHER), tid), None, now, DRAFTS).action is Action.SEND_TEXT


# ---- B07: onboarding ------------------------------------------------------------------------


def test_unset_allowlist_keeps_todays_binding(db):
    fake, client, transports = setup(OPEN)
    _, code = invite(db)
    fake.user_says(OTHER, f"/start {code}")
    poll_once(db, client, OPEN, ist(7, 5))
    assert [t.sender for t in all_rows(db, Tenant)] == [str(OTHER)]
    send_batch(db, transports, DRAFTS, ist(7, 6))
    assert {c["chat_id"] for c in fake.calls} == {str(OTHER)}


def test_outsider_gets_neutral_reply_and_invite_stays_open(db):
    fake, client, transports = setup(LOCKED)
    inv_id, code = invite(db)
    fake.user_says(OTHER, f"/start {code}")  # forwarded deep link
    fake.user_says(OTHER, "Hi again")
    poll_once(db, client, LOCKED, ist(7, 5))
    assert all_rows(db, Tenant) == []
    with db() as s:
        assert s.get(Invite, inv_id).state == "open"
        assert {i.handled_as for i in all_rows(db, Inbound)} == {
            "not_allowed",
            "not_allowed_quiet",  # same 24h neutral limit as an invalid invite
        }
    (row,) = all_rows(db, Outbox)
    assert (row.kind, row.body, row.tenant_id) == ("neutral", NEUTRAL, None)
    send_batch(db, transports, DRAFTS, ist(7, 6))
    assert [(c["chat_id"], c["text"]) for c in fake.calls] == [(str(OTHER), NEUTRAL)]

    # the same, still-open invite then binds the allowed chat
    fake.user_says(ME, f"/start {code}")
    poll_once(db, client, LOCKED, ist(7, 10))
    assert [t.sender for t in all_rows(db, Tenant)] == [str(ME)]
    with db() as s:
        assert s.get(Invite, inv_id).state == "consumed"


def test_allowed_chat_binds_and_gets_welcome(db):
    fake, client, transports = setup(LOCKED)
    _, code = invite(db)
    fake.user_says(ME, f"/start {code}")
    poll_once(db, client, LOCKED, ist(7, 5))
    (t,) = all_rows(db, Tenant)
    assert t.sender == str(ME)
    send_batch(db, transports, DRAFTS, ist(7, 6))
    assert {r.kind: r.state for r in all_rows(db, Outbox)} == {"welcome": "SENT", "opt_in": "SENT"}


def _opted_in(db, fake, client, who):
    _, code = invite(db)
    fake.user_says(who, f"/start {code}")
    fake.user_says(who, "YES")
    poll_once(db, client, OPEN, ist(7, 5))


def test_existing_tenant_not_on_list_gets_no_proactive_send(db, mock_calendar):
    fake, client, transports = setup(OPEN)
    _opted_in(db, fake, client, OTHER)  # bound before the list was set
    send_batch(db, transports, DRAFTS, ist(7, 6))
    n = len(fake.calls)
    # now the owner sets the list without OTHER
    _, client2, locked = setup(LOCKED, fake)  # the owner sets the list; same bot
    with db() as s:
        plan_day(s, mock_calendar, MOCK_DAY, ist(7, 20))
        s.commit()
    assert run_one(db, deps(Clock(ist(8, 0)), mock_calendar), "w1") == "done"
    send_batch(db, locked, DRAFTS, ist(8, 1))
    parts = [r for r in all_rows(db, Outbox) if r.kind == "report_part"]
    assert parts and all(p.state == "CANCELLED" for p in parts)
    assert all("TELEGRAM_ALLOWED_CHAT_IDS" in p.error for p in parts)
    assert len(fake.calls) == n  # nothing sent to OTHER after the list was set
    # STOP is still honoured (review fix): opted out, silently, nothing else routed
    rows_before = len(all_rows(db, Outbox))
    fake.user_says(OTHER, "STOP")
    poll_once(db, client2, LOCKED, ist(8, 5))
    (t,) = all_rows(db, Tenant)
    assert t.opt_in_state == "stopped"  # re-adding the chat later sends nothing
    assert len(all_rows(db, Outbox)) == rows_before  # no STOP ack, no neutral reply
    with db() as s:
        inbound = s.execute(select(Inbound).where(Inbound.handled_as.like("not_allowed%")))
        assert inbound.scalar_one().tenant_id is None


@pytest.mark.parametrize("cmd", ["STOP", "/stop", " stop "])
def test_blocked_tenant_stop_forms_are_honoured(db, cmd):
    fake, client, _ = setup(OPEN)
    _opted_in(db, fake, client, OTHER)
    rows_before = len(all_rows(db, Outbox))
    _, client2, _ = setup(LOCKED, fake)
    fake.user_says(OTHER, cmd)
    poll_once(db, client2, LOCKED, ist(8, 5))
    (t,) = all_rows(db, Tenant)
    assert t.opt_in_state == "stopped"
    assert len(all_rows(db, Outbox)) == rows_before
    assert {i.handled_as for i in all_rows(db, Inbound)} >= {"not_allowed"}


def test_blocked_tenant_start_is_not_honoured(db):
    fake, client, _ = setup(OPEN)
    _opted_in(db, fake, client, OTHER)
    _, client2, _ = setup(LOCKED, fake)
    fake.user_says(OTHER, "STOP")
    poll_once(db, client2, LOCKED, ist(8, 5))
    for i, cmd in enumerate(["START", "/start", "YES"]):
        fake.user_says(OTHER, cmd)
        poll_once(db, client2, LOCKED, ist(9, i))
    (t,) = all_rows(db, Tenant)
    assert t.opt_in_state == "stopped"  # still opted out
    kinds = [r.kind for r in all_rows(db, Outbox)]
    assert "start" not in kinds and kinds.count("neutral") == 1  # neutral, 24h limit


def test_non_tenant_stop_gets_only_the_neutral_reply(db):
    fake, client, _ = setup(LOCKED)
    fake.user_says(OTHER, "STOP")
    poll_once(db, client, LOCKED, ist(8, 5))
    assert all_rows(db, Tenant) == []
    assert [r.kind for r in all_rows(db, Outbox)] == ["neutral"]


def test_allowed_tenant_still_gets_report(db, mock_calendar):
    fake, client, _ = setup(OPEN)
    _opted_in(db, fake, client, ME)
    _, _, locked = setup(LOCKED, fake)
    with db() as s:
        plan_day(s, mock_calendar, MOCK_DAY, ist(7, 20))
        s.commit()
    assert run_one(db, deps(Clock(ist(8, 0)), mock_calendar), "w1") == "done"
    send_batch(db, locked, DRAFTS, ist(8, 1))
    parts = [r for r in all_rows(db, Outbox) if r.kind == "report_part"]
    assert [p.state for p in parts] == ["SENT", "SENT", "SENT"]


def test_whatsapp_is_unaffected_by_the_telegram_allowlist(db, wa_settings):
    from desk.onboarding.service import handle_message
    from tests.whatsapp_helpers import inbound

    with db() as s:
        _, code = create_invite(
            s, business_phone_id=wa_settings.phone_number_id, now=ist(7, 0), ttl=timedelta(1)
        )
        s.commit()
    msg = inbound(wa_settings, "919800000001", f"Hi {code}", "wamid.B07")
    with db() as s:
        assert handle_message(s, wa_settings, msg, ist(7, 5)).handled_as == "bound"
        s.commit()
    # a WhatsApp transport never carries an allowlist
    assert getattr(WhatsAppTransport(FakeGraph().client()), "allowed_recipients", None) is None


# ---- B08: fake WhatsApp ---------------------------------------------------------------------


def test_fake_whatsapp_refuses_a_real_access_token(settings):
    with pytest.raises(GateBlockedError, match="WHATSAPP_ACCESS_TOKEN"):
        make_send_client(settings)  # conftest settings carry a (random) access token
    no_token = replace(settings, whatsapp=replace(settings.whatsapp, access_token=""))
    assert make_send_client(no_token).phone_number_id == MOCK_PHONE_NUMBER_ID


def test_fake_whatsapp_uses_its_own_phone_id():
    real = load_settings({"WHATSAPP_PHONE_NUMBER_ID": "555000111"})
    assert make_client(real).phone_number_id == MOCK_PHONE_NUMBER_ID
    assert WhatsAppTransport(make_client(real)).endpoint == MOCK_PHONE_NUMBER_ID


def test_serve_refuses_to_start_with_whatsapp_token(monkeypatch, pg_url, migrated):
    from desk.__main__ import main

    for k in (
        "TELEGRAM_BOT_TOKEN",
        "DESK_TELEGRAM_LIVE",
        "TELEGRAM_ALLOWED_CHAT_IDS",
        "DESK_ENCRYPT_SENDERS",
        "DESK_SENDER_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DESK_DATABASE_URL", pg_url)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "x" * 24)
    monkeypatch.setattr(sys, "argv", ["desk", "serve", "--once"])
    with pytest.raises(SystemExit) as exc:
        main()
    msg = str(exc.value.code)
    assert "refusing to start" in msg and "WHATSAPP_ACCESS_TOKEN" in msg and "x" * 24 not in msg


def test_fake_whatsapp_never_claims_a_real_phone_ids_rows(db):
    real = load_settings({"WHATSAPP_PHONE_NUMBER_ID": "555000111"})
    client = make_client(real)
    assert client.phone_number_id == MOCK_PHONE_NUMBER_ID
    with db() as s:
        s.add(
            Outbox(
                id=uuid.uuid4(),
                idempotency_key="b08-real",
                business_phone_id="555000111",
                recipient="919800000001",
                kind="neutral",
                body="x",
                state="PENDING",
                created_at=datetime.now(UTC),
                attempts=0,
            )
        )
        s.commit()
    stats = send_batch(db, {"whatsapp": WhatsAppTransport(client)}, DRAFTS, datetime.now(UTC))
    (row,) = all_rows(db, Outbox)
    assert row.state == "PENDING" and row.provider_message_id is None
    assert "ACCEPTED" not in stats.counts
