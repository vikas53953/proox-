"""GATES.md T1 invite pre-binding rule for Telegram.

`python -m desk invite --channel telegram --chat-id <int>` stores the expected chat id in
the invite's bound_sender (a TransportId column: encrypted at rest when B02 is on). Only
that chat can consume the code; any other chat gets the same neutral reply as an invalid
invite and the invite stays open. TELEGRAM_REQUIRE_PREBIND=1 (off by default) makes
`invite` refuse Telegram invites without --chat-id and onboarding refuse unbound ones."""

import itertools
import os
import sys
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from desk import pii
from desk.config import SettingsError, TelegramSettings, load_settings
from desk.db.models import Invite, Outbox, Tenant
from desk.onboarding.invites import create_invite, telegram_chat_id
from desk.onboarding.messages import NEUTRAL
from desk.onboarding.service import handle_message
from desk.outbox.sender import send_batch
from desk.transport.telegram.client import FakeTelegram
from desk.transport.telegram.poller import poll_once
from tests.delivery_helpers import DRAFTS, ist
from tests.whatsapp_helpers import inbound

BOT = FakeTelegram()
ME, OTHER = 5550001, 5550002
OFF = TelegramSettings(bot_token=f"{BOT.bot_id}:0", bot_username=BOT.username)
REQUIRE = replace(OFF, require_prebind=True)
_UPDATE_IDS = itertools.count(5000)


@pytest.fixture(autouse=True)
def restore_cipher():
    yield
    pii.configure(False)


def setup():
    fake = FakeTelegram(bot_id=BOT.bot_id)
    transport, client = fake.transport(OFF.bot_token)
    return fake, client, {"telegram": transport}


def tg_invite(db, chat_id=None):
    with db() as s:
        inv, code = create_invite(
            s,
            business_phone_id=OFF.bot_id,
            now=ist(7, 0),
            ttl=timedelta(hours=24),
            channel="telegram",
            bound_sender=chat_id,
        )
        s.commit()
        return inv.id, code


def invite_row(db, invite_id):
    with db() as s:
        return s.get(Invite, invite_id)


def tenants(db):
    with db() as s:
        return list(s.execute(select(Tenant)).scalars())


def redeem(db, tg, chat_id, code):
    """One /start from chat_id, polled and sent; returns texts that chat received."""
    fake, client, transports = setup()
    fake.user_says(chat_id, f"/start {code}", update_id=next(_UPDATE_IDS))  # cursor moves on
    poll_once(db, client, tg, ist(7, 5))
    send_batch(db, transports, DRAFTS, ist(7, 6))
    return [c["text"] for c in fake.calls if c["method"] == "sendMessage"]


# ---- settings ---------------------------------------------------------------------------


def test_require_prebind_defaults_on_and_parses():
    # owner 2026-10-04: on by default (was off)
    for env in ({}, {"TELEGRAM_REQUIRE_PREBIND": ""}, {"TELEGRAM_REQUIRE_PREBIND": "1"}):
        assert load_settings(env).telegram.require_prebind is True
    assert load_settings({"TELEGRAM_REQUIRE_PREBIND": "0"}).telegram.require_prebind is False


@pytest.mark.parametrize("raw", ["yes", "true", "2", "on"])
def test_require_prebind_bad_value_refuses_to_start(raw):
    with pytest.raises(SettingsError, match="TELEGRAM_REQUIRE_PREBIND"):
        load_settings({"TELEGRAM_REQUIRE_PREBIND": raw})


def test_env_example_has_off_placeholder():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".env.example")) as f:
        assert "TELEGRAM_REQUIRE_PREBIND=0\n" in f.read()


def test_chat_id_normalised_and_checked():
    assert telegram_chat_id(" 0042 ") == "42"
    assert telegram_chat_id(-100123) == "-100123"
    for bad in ("abc", "1.5", "", "12 34", "0x10"):
        with pytest.raises(ValueError):
            telegram_chat_id(bad)


def test_create_invite_normalises_and_refuses_bad_chat_id(db):
    invite_id, _ = tg_invite(db, chat_id=f" 00{ME}")
    assert invite_row(db, invite_id).bound_sender == str(ME)
    with pytest.raises(ValueError):
        tg_invite(db, chat_id="@someone")


# ---- onboarding -------------------------------------------------------------------------


def test_unbound_invite_unchanged_anyone_can_bind(db):
    invite_id, code = tg_invite(db)
    texts = redeem(db, OFF, OTHER, code)
    assert NEUTRAL not in texts and len(texts) == 2  # welcome + opt-in, as today
    assert [t.sender for t in tenants(db)] == [str(OTHER)]
    assert invite_row(db, invite_id).state == "consumed"


def test_prebound_invite_other_chat_gets_neutral_and_invite_stays_open(db):
    invite_id, code = tg_invite(db, chat_id=ME)
    assert invite_row(db, invite_id).bound_sender == str(ME)
    texts = redeem(db, OFF, OTHER, code)
    assert texts == [NEUTRAL]  # exactly what an invalid code gets: no leak
    assert tenants(db) == []
    assert invite_row(db, invite_id).state == "open"
    # the right chat can still use it afterwards
    texts = redeem(db, OFF, ME, code)
    assert NEUTRAL not in texts and len(texts) == 2
    (tenant,) = tenants(db)
    assert tenant.sender == str(ME)
    row = invite_row(db, invite_id)
    assert row.state == "consumed" and row.consumed_by == tenant.id


def test_wrong_chat_reply_identical_to_invalid_code(db):
    _, code = tg_invite(db, chat_id=ME)
    wrong_chat = redeem(db, OFF, OTHER, code)
    invalid = redeem(db, OFF, OTHER + 1, "PX-" + "A" * 26)
    assert wrong_chat == invalid == [NEUTRAL]
    with db() as s:
        kinds = s.execute(select(Outbox.kind)).scalars().all()
    assert sorted(kinds) == ["neutral", "neutral"]


def test_prebound_chat_id_encrypted_at_rest_with_b02(db):
    pii.configure(True, os.urandom(pii.KEY_BYTES))
    invite_id, code = tg_invite(db, chat_id=ME)
    with db() as s:
        raw = s.execute(text("SELECT bound_sender FROM invites")).scalar_one()
    assert raw and str(ME) not in raw
    assert redeem(db, OFF, OTHER, code) == [NEUTRAL]
    assert invite_row(db, invite_id).state == "open"
    assert len(redeem(db, OFF, ME, code)) == 2
    assert invite_row(db, invite_id).state == "consumed"


def test_require_prebind_refuses_unbound_invite(db):
    invite_id, code = tg_invite(db)
    assert redeem(db, REQUIRE, ME, code) == [NEUTRAL]
    assert tenants(db) == [] and invite_row(db, invite_id).state == "open"


def test_require_prebind_accepts_prebound_invite(db):
    invite_id, code = tg_invite(db, chat_id=ME)
    assert len(redeem(db, REQUIRE, ME, code)) == 2
    assert invite_row(db, invite_id).state == "consumed"


def test_require_prebind_never_touches_whatsapp(db, wa_settings):
    with db() as s:
        _, code = create_invite(
            s,
            business_phone_id=wa_settings.phone_number_id,
            now=ist(7, 0),
            ttl=timedelta(hours=24),
        )
        s.commit()
    wa = replace(wa_settings)
    object.__setattr__(wa, "require_prebind", True)  # even if a settings object carried it
    with db() as s:
        msg = inbound(wa, "919800000001", f"Hi {code}", "wamid.P1")
        out = handle_message(s, wa, msg, ist(7, 1))
        s.commit()
    assert out.handled_as == "bound"


# ---- CLI --------------------------------------------------------------------------------


def run_invite(monkeypatch, capsys, pg_url, env, *argv):
    from desk.__main__ import main

    for k in list(os.environ):
        if k.startswith(("DESK_", "TELEGRAM_", "WHATSAPP_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DESK_DATABASE_URL", pg_url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", OFF.bot_token)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", OFF.bot_username)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "argv", ["desk", "invite", *argv])
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        print(exc.code)
    return code, capsys.readouterr().out


def invites(db):
    with db() as s:
        return list(s.execute(select(Invite)).scalars())


def test_cli_chat_id_prebinds(db, pg_url, monkeypatch, capsys):
    code, out = run_invite(
        monkeypatch, capsys, pg_url, {}, "--channel", "telegram", "--chat-id", str(ME)
    )
    assert code == 0 and "pre-bound" in out and str(ME) not in out
    (row,) = invites(db)
    assert row.bound_sender == str(ME) and row.channel == "telegram"


def test_cli_without_chat_id_unchanged(db, pg_url, monkeypatch, capsys):
    env = {"TELEGRAM_REQUIRE_PREBIND": "0"}  # owner 2026-10-04: default is now on
    code, out = run_invite(monkeypatch, capsys, pg_url, env, "--channel", "telegram")
    assert code == 0 and "pre-bound" not in out
    (row,) = invites(db)
    assert row.bound_sender is None


def test_cli_require_prebind_refuses_without_chat_id(db, pg_url, monkeypatch, capsys):
    env = {"TELEGRAM_REQUIRE_PREBIND": "1"}
    code, out = run_invite(monkeypatch, capsys, pg_url, env, "--channel", "telegram")
    assert code != 0 and "TELEGRAM_REQUIRE_PREBIND" in out
    assert invites(db) == []
    code, _ = run_invite(
        monkeypatch, capsys, pg_url, env, "--channel", "telegram", "--chat-id", str(ME)
    )
    assert code == 0 and len(invites(db)) == 1


def test_cli_chat_id_refused_for_whatsapp(db, pg_url, monkeypatch, capsys):
    code, out = run_invite(
        monkeypatch, capsys, pg_url, {}, "--phone-number-id", "100", "--chat-id", "5"
    )
    assert code != 0 and "Telegram invites only" in out
    assert invites(db) == []
