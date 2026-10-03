"""GATES.md T1: DRAFT Telegram privacy disclosure behind TELEGRAM_PRIVACY_NOTICE (off by
default). Off = onboarding unchanged; on = one notice right after the Telegram welcome,
never twice per tenant, never on WhatsApp."""

import os
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from desk import pii
from desk.config import SettingsError, TelegramSettings, load_settings
from desk.db.models import Outbox, Tenant
from desk.onboarding import messages as msgs
from desk.onboarding.invites import create_invite
from desk.onboarding.service import _privacy_notice, handle_message
from desk.outbox.sender import send_batch
from desk.transport.telegram.client import FakeTelegram
from desk.transport.telegram.poller import poll_once
from tests.delivery_helpers import DRAFTS, ist
from tests.whatsapp_helpers import inbound

BOT = FakeTelegram()
ME = 5550001
OFF = TelegramSettings(bot_token=f"{BOT.bot_id}:0", bot_username=BOT.username)
ON = replace(OFF, privacy_notice=True)


@pytest.fixture(autouse=True)
def restore_cipher():
    yield
    pii.configure(False)


def setup():
    fake = FakeTelegram(bot_id=BOT.bot_id)
    transport, client = fake.transport(OFF.bot_token)
    return fake, client, {"telegram": transport}


def tg_invite(db):
    with db() as s:
        _, code = create_invite(
            s,
            business_phone_id=OFF.bot_id,
            now=ist(7, 0),
            ttl=timedelta(hours=24),
            channel="telegram",
        )
        s.commit()
    return code


def outbox(db):
    with db() as s:
        rows = s.execute(select(Outbox).order_by(Outbox.created_at)).scalars()
        return list(rows)


def bind(db, tg, *, then=()):
    fake, client, transports = setup()
    fake.user_says(ME, f"/start {tg_invite(db)}")
    for text in then:
        fake.user_says(ME, text)
    poll_once(db, client, tg, ist(7, 5))
    send_batch(db, transports, DRAFTS, ist(7, 6))
    texts = [c["text"] for c in fake.calls if c["method"] == "sendMessage"]
    return fake, client, transports, texts


# ---- settings ---------------------------------------------------------------------------


def test_setting_defaults_off_and_parses():
    for env in ({}, {"TELEGRAM_PRIVACY_NOTICE": ""}, {"TELEGRAM_PRIVACY_NOTICE": "0"}):
        assert load_settings(env).telegram.privacy_notice is False
    assert load_settings({"TELEGRAM_PRIVACY_NOTICE": " 1 "}).telegram.privacy_notice is True


@pytest.mark.parametrize("raw", ["yes", "true", "2", "on"])
def test_bad_value_refuses_to_start(raw):
    with pytest.raises(SettingsError, match="TELEGRAM_PRIVACY_NOTICE"):
        load_settings({"TELEGRAM_PRIVACY_NOTICE": raw})


def test_env_example_has_off_placeholder():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".env.example")) as f:
        assert "TELEGRAM_PRIVACY_NOTICE=0\n" in f.read()


# ---- text -------------------------------------------------------------------------------


def test_text_is_marked_draft_and_says_what_t1_requires():
    assert msgs.PRIVACY_NOTICE_STATUS == "DRAFT"
    for encrypted in (True, False):
        text = msgs.privacy_notice_telegram(encrypted)
        assert "end-to-end encrypted nahi" in text  # Roman Hinglish
        assert "not end-to-end encrypted" in text and "Telegram stores them" in text
        assert "STOP" in text and "{" not in text
    assert "encrypted." in msgs.privacy_notice_telegram(True)
    # never claims encryption at rest while B02 is off
    assert "chat id encrypted" not in msgs.privacy_notice_telegram(False)


# ---- onboarding -------------------------------------------------------------------------


def test_off_onboarding_unchanged(db):
    _, _, _, texts = bind(db, OFF)
    assert [r.kind for r in outbox(db)] == ["welcome", "opt_in"]
    assert texts == [
        msgs.WELCOME_PENDING.format(reason=msgs.PENDING_REASON),
        msgs.OPT_IN,
    ]


def test_on_one_notice_right_after_welcome(db):
    _, _, _, texts = bind(db, ON)
    assert [r.kind for r in outbox(db)] == ["welcome", "privacy_notice", "opt_in"]
    assert texts == [
        msgs.WELCOME_PENDING.format(reason=msgs.PENDING_REASON),
        msgs.privacy_notice_telegram(encrypted=False),
        msgs.OPT_IN,
    ]


def test_on_with_b02_says_encrypted(db):
    pii.configure(True, os.urandom(pii.KEY_BYTES))
    _, _, _, texts = bind(db, ON)
    assert texts[1] == msgs.privacy_notice_telegram(encrypted=True)


def test_never_twice_per_tenant(db):
    fake, client, transports, _ = bind(db, ON, then=["YES"])
    # the same chat opens a second invite link, greets, stops and restarts
    fake.user_says(ME, f"/start {tg_invite(db)}")
    for text in ("hi", "STOP", "START", "/start"):
        fake.user_says(ME, text)
    poll_once(db, client, ON, ist(7, 30))
    send_batch(db, transports, DRAFTS, ist(7, 31))
    assert [r.kind for r in outbox(db)].count("privacy_notice") == 1
    assert [r.kind for r in outbox(db)].count("welcome") == 1
    sent = [c["text"] for c in fake.calls if c["method"] == "sendMessage"]
    assert sum(t.startswith("Privacy:") for t in sent) == 1
    # a direct second call for the same tenant is a no-op (per-tenant key)
    (row,) = [r for r in outbox(db) if r.kind == "privacy_notice"]
    with db() as s:
        tenant = s.get(Tenant, row.tenant_id)
        msg = type("M", (), {"channel": "telegram", "message_id": "x2", "sender": str(ME)})
        msg.phone_number_id = OFF.bot_id
        _privacy_notice(s, ON, msg, tenant, ist(7, 40))
        s.commit()
    assert [r.kind for r in outbox(db)].count("privacy_notice") == 1


class _WhatsAppWithFlag:
    """WhatsApp settings that (wrongly) carry the flag: the notice must still not go out."""

    privacy_notice = True

    def __init__(self, wa):
        self._wa = wa

    def accepts(self, msg):
        return self._wa.accepts(msg)


def test_never_for_whatsapp(db, wa_settings):
    with db() as s:
        _, code = create_invite(
            s, business_phone_id=wa_settings.phone_number_id, now=ist(7, 0), ttl=timedelta(1)
        )
        s.commit()
    msg = inbound(wa_settings, "919800000001", f"Hi {code}", "wamid.PRIV1")
    with db() as s:
        assert handle_message(s, _WhatsAppWithFlag(wa_settings), msg, ist(7, 5)).handled_as == (
            "bound"
        )
        s.commit()
    assert [r.kind for r in outbox(db)] == ["welcome", "opt_in"]
