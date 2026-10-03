"""GATES.md T1 known limit "Blocking is noticed late": opt-in early detection behind
TELEGRAM_TRACK_MEMBER_UPDATES (off by default).

Off: the getUpdates request is byte-identical to before and member updates are ignored.
On: `my_chat_member` is requested; a private-chat "kicked" update (the person blocked the
bot) stops that tenant's opt-in exactly like a 403 on send and cancels its pending
proactive rows; "member" again does NOT resubscribe (START does, as today). Deduplicated
in inbound_messages, so a re-delivered update never undoes a later START."""

import json
import os
import uuid
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import select, text

from desk import pii
from desk.config import SettingsError, TelegramSettings, load_settings
from desk.db.models import Inbound, Outbox, Tenant
from desk.onboarding.service import BLOCKED_REASON
from desk.outbox.sender import send_batch
from desk.transport.telegram.client import FakeTelegram, TelegramClient
from desk.transport.telegram.poller import poll_once
from tests.delivery_helpers import DRAFTS, ist

BOT = FakeTelegram()
ME, OTHER = 5550001, 5550002
OFF = TelegramSettings(bot_token=f"{BOT.bot_id}:0", bot_username=BOT.username)
ON = replace(OFF, track_member_updates=True)


@pytest.fixture(autouse=True)
def restore_cipher():
    yield
    pii.configure(False)


class Stub:
    """A client that returns the given updates whatever the request (as if Telegram
    re-delivered them), to test what the poller does with them."""

    def __init__(self, *batches):
        self.batches = list(batches)

    def get_updates(self, offset, timeout_s=25, member_updates=False):
        return self.batches.pop(0) if self.batches else []


def kicked(chat_id=ME, update_id=1, status="kicked", chat_type="private", date=1790000100):
    fake = FakeTelegram(bot_id=BOT.bot_id)
    fake.member_update(chat_id, status, chat_type=chat_type, date=date, update_id=update_id)
    return fake.updates[0]


def message(chat_id, text_, update_id, message_id):
    fake = FakeTelegram(bot_id=BOT.bot_id)
    fake.user_says(chat_id, text_, update_id=update_id, message_id=message_id)
    return fake.updates[0]


def add_tg_tenant(db, chat_id=ME, opt_in="yes"):
    with db() as s:
        t = Tenant(
            id=uuid.uuid4(),
            channel="telegram",
            business_phone_id=OFF.bot_id,
            sender=str(chat_id),
            opt_in_state=opt_in,
            state="pending",
            created_at=ist(7, 0),
            last_inbound_at=ist(7, 0),
        )
        s.add(t)
        s.commit()
        return t.id


def add_row(db, tenant_id, state, proactive=True, chat_id=ME):
    with db() as s:
        row = Outbox(
            id=uuid.uuid4(),
            idempotency_key=f"t:{uuid.uuid4()}",
            tenant_id=tenant_id,
            channel="telegram",
            business_phone_id=OFF.bot_id,
            recipient=str(chat_id),
            kind="report_part" if proactive else "question_pending",
            body="x",
            state=state,
            created_at=ist(7, 1),
            proactive=proactive,
        )
        s.add(row)
        s.commit()
        return row.id


def tenant(db, tenant_id):
    with db() as s:
        return s.get(Tenant, tenant_id)


def row(db, row_id):
    with db() as s:
        return s.get(Outbox, row_id)


# ---- settings ---------------------------------------------------------------------------


def test_setting_defaults_off_and_parses():
    for env in ({}, {"TELEGRAM_TRACK_MEMBER_UPDATES": ""}, {"TELEGRAM_TRACK_MEMBER_UPDATES": "0"}):
        assert load_settings(env).telegram.track_member_updates is False
    on = load_settings({"TELEGRAM_TRACK_MEMBER_UPDATES": "1"}).telegram
    assert on.track_member_updates is True


@pytest.mark.parametrize("raw", ["yes", "true", "2", "on"])
def test_bad_value_refuses_to_start(raw):
    with pytest.raises(SettingsError, match="TELEGRAM_TRACK_MEMBER_UPDATES"):
        load_settings({"TELEGRAM_TRACK_MEMBER_UPDATES": raw})


def test_env_example_has_off_placeholder():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".env.example")) as f:
        assert "TELEGRAM_TRACK_MEMBER_UPDATES=0\n" in f.read()


# ---- request ----------------------------------------------------------------------------


def legacy_body(offset: int, timeout_s: int) -> bytes:
    """The getUpdates body as built before this change."""
    data = {"offset": offset, "timeout": timeout_s, "allowed_updates": json.dumps(["message"])}
    return httpx.Request("POST", "https://example.invalid/getUpdates", data=data).content


def test_flag_off_request_is_byte_identical(db):
    fake = FakeTelegram(bot_id=BOT.bot_id)
    _, client = fake.transport(OFF.bot_token)
    poll_once(db, client, OFF, ist(7, 5))
    assert fake.poll_requests == [legacy_body(0, 0)]


def test_flag_on_requests_my_chat_member(db):
    fake = FakeTelegram(bot_id=BOT.bot_id)
    _, client = fake.transport(OFF.bot_token)
    poll_once(db, client, ON, ist(7, 5))
    form = dict(httpx.QueryParams(fake.poll_requests[0].decode()))
    assert json.loads(form["allowed_updates"]) == ["message", "my_chat_member"]


# ---- behaviour --------------------------------------------------------------------------


def test_flag_off_member_update_ignored(db):
    tid = add_tg_tenant(db)
    pending = add_row(db, tid, "PENDING")
    result = poll_once(db, Stub([kicked()]), OFF, ist(7, 5))
    assert result.skipped == 1 and result.blocked == 0
    assert tenant(db, tid).opt_in_state == "yes"
    assert row(db, pending).state == "PENDING"


def test_kicked_stops_and_cancels_pending_proactive(db):
    tid = add_tg_tenant(db)
    pending = add_row(db, tid, "PENDING")
    waiting = add_row(db, tid, "WAITING_WINDOW")
    sending = add_row(db, tid, "SENDING")
    sent = add_row(db, tid, "SENT")
    reply = add_row(db, tid, "PENDING", proactive=False)
    other_tid = add_tg_tenant(db, OTHER)
    others = add_row(db, other_tid, "PENDING", chat_id=OTHER)
    fake = FakeTelegram(bot_id=BOT.bot_id)
    fake.member_update(ME, "kicked")
    _, client = fake.transport(OFF.bot_token)
    result = poll_once(db, client, ON, ist(7, 5))
    assert result.blocked == 1
    assert tenant(db, tid).opt_in_state == "stopped"  # same as a 403 on send
    for rid in (pending, waiting):
        r = row(db, rid)
        assert r.state == "CANCELLED" and r.error == BLOCKED_REASON
    assert row(db, sending).state == "SENDING"  # in flight: left alone
    assert row(db, sent).state == "SENT"
    assert row(db, reply).state == "PENDING"  # only proactive rows
    assert tenant(db, other_tid).opt_in_state == "yes"
    assert row(db, others).state == "PENDING"
    with db() as s:
        (inb,) = s.execute(select(Inbound)).scalars()
    assert inb.handled_as == "blocked" and inb.tenant_id == tid
    assert inb.message_id.startswith(f"tg:{OFF.bot_id}:{ME}:member:")


def test_same_opt_out_as_403_on_send(db):
    tid = add_tg_tenant(db)
    add_row(db, tid, "PENDING", proactive=False)
    fake = FakeTelegram(bot_id=BOT.bot_id)
    fake.script = [{"error_code": 403, "description": "Forbidden: bot was blocked by the user"}]
    transport, _ = fake.transport(OFF.bot_token)
    send_batch(db, {"telegram": transport}, DRAFTS, ist(7, 6))
    via_403 = tenant(db, tid).opt_in_state
    tid2 = add_tg_tenant(db, OTHER)
    poll_once(db, Stub([kicked(OTHER)]), ON, ist(7, 7))
    assert tenant(db, tid2).opt_in_state == via_403 == "stopped"


def test_member_again_does_not_resubscribe_start_does(db):
    tid = add_tg_tenant(db)
    poll_once(db, Stub([kicked(update_id=1)]), ON, ist(7, 5))
    poll_once(db, Stub([kicked(update_id=2, status="member", date=1790000200)]), ON, ist(7, 6))
    assert tenant(db, tid).opt_in_state == "stopped"
    poll_once(db, Stub([message(ME, "START", 3, 77)]), ON, ist(7, 7))
    assert tenant(db, tid).opt_in_state == "asked"


def test_redelivered_kick_never_undoes_a_later_start(db):
    tid = add_tg_tenant(db)
    batch = [kicked(update_id=10), message(ME, "START", 11, 88)]
    poll_once(db, Stub(batch), ON, ist(7, 5))
    assert tenant(db, tid).opt_in_state == "asked"
    # crash before the cursor was saved: Telegram re-delivers (update ids may differ)
    again = [kicked(update_id=500), message(ME, "START", 501, 88)]
    result = poll_once(db, Stub(again), ON, ist(7, 6))
    assert result.blocked == 0
    assert tenant(db, tid).opt_in_state == "asked"


@pytest.mark.parametrize(
    "update",
    [
        kicked(chat_type="group"),
        kicked(chat_type="supergroup"),
        kicked(status="left"),
        kicked(status="member"),
    ],
)
def test_other_member_updates_change_nothing(db, update):
    tid = add_tg_tenant(db)
    pending = add_row(db, tid, "PENDING")
    result = poll_once(db, Stub([update]), ON, ist(7, 5))
    assert result.blocked == 0
    assert tenant(db, tid).opt_in_state == "yes"
    assert row(db, pending).state == "PENDING"


def test_kick_from_unknown_chat_is_recorded_only(db):
    result = poll_once(db, Stub([kicked(OTHER)]), ON, ist(7, 5))
    assert result.blocked == 0
    with db() as s:
        (inb,) = s.execute(select(Inbound)).scalars()
        assert inb.handled_as == "blocked_no_tenant" and inb.tenant_id is None
        assert s.execute(select(Tenant)).first() is None


def test_works_with_b02_and_key_encrypted(db):
    pii.configure(True, os.urandom(pii.KEY_BYTES))
    tid = add_tg_tenant(db)
    pending = add_row(db, tid, "PENDING")
    poll_once(db, Stub([kicked()]), ON, ist(7, 5))
    assert tenant(db, tid).opt_in_state == "stopped"
    assert row(db, pending).state == "CANCELLED"
    with db() as s:
        raw = s.execute(text("SELECT message_id, sender FROM inbound_messages")).one()
    assert all(str(ME) not in v for v in raw)


def test_real_client_signature_unchanged_by_default():
    """Older callers: get_updates(offset, timeout_s) still sends only "message"."""
    fake = FakeTelegram(bot_id=BOT.bot_id)
    client: TelegramClient = fake.client(OFF.bot_token)
    client.get_updates(offset=7, timeout_s=3)
    assert fake.poll_requests == [legacy_body(7, 3)]
