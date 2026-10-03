"""B02: transport ids (WhatsApp wa_id, Telegram chat id) encrypted at rest behind
DESK_ENCRYPT_SENDERS (OFF by default).

OFF = stored rows exactly as before. ON = no covered column holds an id in plain text
(checked with raw SQL, bypassing the ORM), while onboarding, lookups, sends and receipts
work unchanged. Misconfiguration refuses to start; a stored ciphertext is never handed
back as an id; the key never reaches logs."""

import base64
import dataclasses
import logging
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text

from desk import pii
from desk.app import create_app
from desk.config import SenderCryptoSettings, load_settings
from desk.db.models import DeliveryReceipt, Inbound, Invite, Outbox, Tenant
from desk.logsafe import REDACTED, install, redact
from desk.onboarding.invites import create_invite
from desk.outbox.receipts import apply_status
from desk.outbox.sender import send_batch
from desk.transport.telegram.client import FakeTelegram
from desk.transport.telegram.poller import poll_once
from desk.transport.whatsapp.client import FakeGraph
from desk.transport.whatsapp.payload import StatusUpdate
from tests.delivery_helpers import DRAFTS, add_tenant, ist
from tests.whatsapp_helpers import message, payload, post

ALICE, BOB = "919800000001", "919800000002"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def new_key() -> bytes:
    return os.urandom(pii.KEY_BYTES)


def key_text(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode().rstrip("=")


@pytest.fixture(autouse=True)
def restore_cipher():
    """Every test leaves the process with encryption OFF (the default)."""
    yield
    pii.configure(False)


@pytest.fixture
def key() -> bytes:
    return new_key()


def on(settings, key):
    return dataclasses.replace(settings, senders=SenderCryptoSettings(enabled=True, key=key))


def raw_values(db) -> dict[str, list[str]]:
    """Every covered column, read with raw SQL (no TypeDecorator, no decryption)."""
    out = {}
    with db() as s:
        for table, col in pii.COVERED:
            sql = f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL"  # noqa: S608
            vals = s.execute(text(sql))
            out[f"{table}.{col}"] = [v for (v,) in vals]
    return out


def raw_dump(db) -> str:
    """Every row of every table as text (media blobs excluded: random bytes)."""
    with db() as s:
        tables = s.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'media'")
        ).scalars()
        return "\n".join(
            str(r)
            for t in list(tables)
            for r in s.execute(text(f'SELECT t::text FROM "{t}" t'))  # noqa: S608
        )


def make_invite(db, business_id, *, channel="whatsapp", bound=None, with_code=True):
    with db() as s:
        inv, code = create_invite(
            s,
            business_phone_id=business_id,
            now=datetime.now(UTC),
            ttl=timedelta(hours=24),
            bound_sender=bound,
            with_code=with_code,
            channel=channel,
        )
        s.commit()
        return inv.id, code


def onboard_alice(client, db, wa):
    _, code = make_invite(db, wa.phone_number_id)
    assert post(client, wa, payload(wa, message(ALICE, f"Hi {code}"))).status_code == 200
    assert post(client, wa, payload(wa, message(ALICE, "YES"))).status_code == 200


# ---- flag OFF: stored data exactly as before ---------------------------------------------


def test_flag_off_stores_plain_ids_exactly_as_before(settings, db, wa_settings):
    client = TestClient(create_app(settings=settings, session_factory=db))
    make_invite(db, wa_settings.phone_number_id, bound=BOB, with_code=False)
    onboard_alice(client, db, wa_settings)
    raw = raw_values(db)
    assert not pii.enabled()
    assert raw["tenants.sender"] == [ALICE]
    assert raw["invites.bound_sender"] == [BOB]
    assert set(raw["inbound_messages.sender"]) == {ALICE}
    assert set(raw["outbox.recipient"]) == {ALICE}
    assert all(v.startswith("wamid.") for v in raw["inbound_messages.message_id"])
    assert "enc1:" not in raw_dump(db)
    assert all(len(v) <= 20 for v in raw["tenants.sender"] + raw["outbox.recipient"])


# ---- flag ON: WhatsApp ---------------------------------------------------------------------


def test_flag_on_whatsapp_onboarding_works_and_no_plain_wa_id_is_stored(
    settings, db, wa_settings, key
):
    client = TestClient(create_app(settings=on(settings, key), session_factory=db))
    assert pii.enabled()
    make_invite(db, wa_settings.phone_number_id, bound=BOB, with_code=False)
    onboard_alice(client, db, wa_settings)
    with db() as s:
        (tenant,) = s.execute(select(Tenant)).scalars()
        assert (tenant.sender, tenant.opt_in_state) == (ALICE, "yes")  # YES found the tenant
        assert s.execute(select(func.count()).select_from(Tenant)).scalar() == 1
        assert s.execute(select(Tenant).where(Tenant.sender == ALICE)).scalar_one().id == tenant.id
        assert {r.recipient for r in s.execute(select(Outbox)).scalars()} == {ALICE}
        assert s.execute(select(Invite).where(Invite.bound_sender == BOB)).scalar_one()

    graph = FakeGraph()
    stats = send_batch(db, graph.client(), DRAFTS, datetime.now(UTC))
    assert stats.counts and set(stats.counts) == {"ACCEPTED"}
    assert graph.requests and {g["to"] for g in graph.requests} == {ALICE}

    raw = raw_values(db)
    for col, vals in raw.items():
        for v in vals:
            assert v.startswith("enc1:"), col
            assert ALICE not in v and BOB not in v, col
    assert raw["tenants.sender"] and raw["invites.bound_sender"] and raw["outbox.recipient"]
    assert raw["outbox.provider_message_id"] and raw["inbound_messages.message_id"]
    dump = raw_dump(db)
    assert ALICE not in dump and BOB not in dump  # no other table carries it either


def test_flag_on_receipts_find_the_encrypted_row(db, mock_calendar, key):
    pii.configure(True, key)
    tid = add_tenant(db, ALICE, "yes", ist(7, 0))
    with db() as s:
        s.add(
            Outbox(
                idempotency_key="k-receipt",
                tenant_id=tid,
                business_phone_id="100200300",
                recipient=ALICE,
                kind="reply",
                body="hello",
                created_at=ist(7, 1),
            )
        )
        s.commit()
    graph = FakeGraph()
    send_batch(db, graph.client(), DRAFTS, ist(7, 2))
    with db() as s:
        row = s.execute(select(Outbox)).scalar_one()
        assert row.state == "ACCEPTED" and graph.requests[0]["to"] == ALICE
        st = StatusUpdate(row.provider_message_id, "delivered", ist(7, 3), ALICE, None, None, None)
        apply_status(s, st, ist(7, 3))
        s.commit()
        assert s.get(Outbox, row.id).state == "DELIVERED"
        assert s.execute(select(DeliveryReceipt)).scalar_one().provider_message_id == (
            row.provider_message_id
        )
    raw = raw_values(db)
    assert raw["delivery_receipts.provider_message_id"][0].startswith("enc1:")


# ---- flag ON: Telegram --------------------------------------------------------------------


def test_flag_on_telegram_chat_id_is_encrypted_everywhere(db, key):
    from desk.config import TelegramSettings

    pii.configure(True, key)
    fake = FakeTelegram()
    tg = TelegramSettings(bot_token=f"{fake.bot_id}:0", bot_username=fake.username)
    transport, client = fake.transport(tg.bot_token)
    me = 5550001234
    _, code = make_invite(db, tg.bot_id, channel="telegram")
    fake.user_says(me, f"/start {code}")
    assert poll_once(db, client, tg, ist(7, 5)).handled == 1
    fake.user_says(me, "YES")
    poll_once(db, client, tg, ist(7, 6))
    with db() as s:
        (t,) = s.execute(select(Tenant)).scalars()
        assert (t.channel, t.sender, t.opt_in_state) == ("telegram", str(me), "yes")
        ids = sorted(s.execute(select(Inbound.message_id)).scalars())
        assert ids and all(i.startswith(f"tg:{tg.bot_id}:{me}:") for i in ids)
        assert s.get(Inbound, ids[0]) is not None  # dedupe-key lookup on the encrypted PK
    send_batch(db, {"telegram": transport}, DRAFTS, ist(7, 7))
    assert fake.calls and {c["chat_id"] for c in fake.calls} == {str(me)}
    raw = raw_values(db)
    for col, vals in raw.items():
        assert all(v.startswith("enc1:") and str(me) not in v for v in vals), col
    assert raw["outbox.provider_message_id"]  # "tg:<chat>:<msg>" covered too
    assert str(me) not in raw_dump(db)


# ---- wrong / missing key: clear error, never ciphertext as an id ---------------------------


def test_wrong_key_raises_clear_error_never_returns_ciphertext(db, key):
    pii.configure(True, key)
    add_tenant(db, ALICE)
    stored = raw_values(db)["tenants.sender"][0]
    pii.configure(True, new_key())
    with db() as s, pytest.raises(pii.SenderKeyError, match="wrong DESK_SENDER_KEY") as err:
        s.execute(select(Tenant)).scalars().all()
    assert stored not in str(err.value) and key_text(key) not in str(err.value)
    pii.configure(False)  # flag off = key ignored: encrypted rows cannot be read either
    with db() as s, pytest.raises(pii.SenderKeyError, match="no key is configured"):
        s.execute(select(Tenant)).scalars().all()


@pytest.mark.parametrize(
    "env, match",
    [
        ({"DESK_ENCRYPT_SENDERS": "1"}, "not set"),
        ({"DESK_ENCRYPT_SENDERS": "1", "DESK_SENDER_KEY": ""}, "not set"),
        ({"DESK_ENCRYPT_SENDERS": "1", "DESK_SENDER_KEY": key_text(os.urandom(32))}, "64 bytes"),
        ({"DESK_ENCRYPT_SENDERS": "1", "DESK_SENDER_KEY": "!!" + "a" * 84}, "base64"),
        ({"DESK_ENCRYPT_SENDERS": "yes", "DESK_SENDER_KEY": key_text(b"k" * 64)}, "0 or 1"),
    ],
)
def test_flag_on_without_a_valid_key_refuses_to_start(env, match):
    with pytest.raises(pii.SenderKeyError, match=match) as err:
        load_settings(env)
    if env.get("DESK_SENDER_KEY"):
        assert env["DESK_SENDER_KEY"] not in str(err.value)


def test_flag_off_ignores_the_key_and_valid_key_is_accepted(key):
    s = load_settings({"DESK_SENDER_KEY": "garbage"})
    assert s.senders == SenderCryptoSettings() and s.senders.key is None
    s = load_settings({"DESK_ENCRYPT_SENDERS": "1", "DESK_SENDER_KEY": key_text(key) + "=="})
    assert s.senders.enabled and s.senders.key == key
    assert key_text(key) not in repr(s)


def test_app_refuses_to_start_with_mixed_rows(settings, db, key):
    add_tenant(db, ALICE)  # plaintext (flag off)
    with pytest.raises(pii.SenderStateError, match="senders encrypt"):
        create_app(settings=on(settings, key), session_factory=db)
    pii.configure(True, key)
    with db() as s:
        pii.encrypt_existing(s)
        s.commit()
    with pytest.raises(pii.SenderStateError, match="turn the flag back on"):
        create_app(settings=settings, session_factory=db)  # flag off, rows encrypted


# ---- operator CLI: senders status / encrypt -----------------------------------------------


def run_cli(monkeypatch, capsys, pg_url, env: dict, *argv) -> tuple[int, str]:
    from desk.__main__ import main

    for k in ("DESK_ENCRYPT_SENDERS", "DESK_SENDER_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DESK_DATABASE_URL", pg_url)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "argv", ["desk", *argv])
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        if not isinstance(exc.code, int):
            print(exc.code)
    return code, capsys.readouterr().out


def test_cli_encrypts_mixed_rows_once_and_is_idempotent(
    db, pg_url, key, monkeypatch, capsys, wa_settings
):
    tid = add_tenant(db, ALICE)  # plaintext rows from before the flag
    make_invite(db, wa_settings.phone_number_id, bound=BOB, with_code=False)
    with db() as s:
        s.add(
            Outbox(
                idempotency_key=f"neutral:whatsapp:100200300:{ALICE}:2026-10-01",
                tenant_id=tid,
                business_phone_id="100200300",
                recipient=ALICE,
                kind="neutral",
                body="x",
                created_at=ist(7, 0),
                in_reply_to="wamid.OLD1",
            )
        )
        s.commit()
    pii.configure(True, key)
    add_tenant(db, "919800000009")  # encrypted row: now mixed
    env = {"DESK_ENCRYPT_SENDERS": "1", "DESK_SENDER_KEY": key_text(key)}

    code, out = run_cli(monkeypatch, capsys, pg_url, {}, "senders", "encrypt")
    assert code == 2 and "refusing" in out  # flag off: refused
    code, out = run_cli(monkeypatch, capsys, pg_url, env, "senders", "status")
    assert code == 1 and "MISMATCH" in out and "tenants.sender: plain=1 encrypted=1" in out
    code, out = run_cli(monkeypatch, capsys, pg_url, env, "senders", "encrypt")
    assert code == 0 and "encrypted 5 values" in out and "consistent" in out
    assert key_text(key) not in out
    code, out = run_cli(monkeypatch, capsys, pg_url, env, "senders", "encrypt")
    assert code == 0 and "encrypted 0 values" in out  # idempotent

    raw = raw_values(db)
    for col in ("tenants.sender", "invites.bound_sender", "outbox.recipient"):
        assert raw[col] and all(v.startswith("enc1:") for v in raw[col]), col
    assert all(v.startswith("enc1:") for v in raw["outbox.idempotency_key"])
    assert all(v.startswith("enc1:") for v in raw["outbox.in_reply_to"])
    assert ALICE not in raw_dump(db) and BOB not in raw_dump(db)
    with db() as s:  # ORM lookups find the migrated rows
        assert s.execute(select(Tenant).where(Tenant.sender == ALICE)).scalar_one().id == tid
        assert s.execute(select(Invite).where(Invite.bound_sender == BOB)).scalar_one()
        assert s.execute(select(Outbox)).scalar_one().in_reply_to == "wamid.OLD1"


# ---- logs never carry the key -------------------------------------------------------------


def test_logs_never_print_the_sender_key(caplog, key):
    install()
    pii.configure(True, key)
    padded = base64.urlsafe_b64encode(key).decode()
    std = base64.b64encode(key).decode()
    log = logging.getLogger("desk.test")
    with caplog.at_level(logging.DEBUG):
        log.warning("env dump DESK_SENDER_KEY=%s", padded)
        log.warning("bare %s and %s", key_text(key), std)
        try:
            raise ValueError(f"bad key {padded}")
        except ValueError:
            log.exception("boom")
        log.info("extra", extra={"k": key_text(key)})
    for form in (padded, key_text(key), std):
        assert form not in caplog.text
        assert all(form not in str(vars(r)) for r in caplog.records)
    assert REDACTED in caplog.text
    other = key_text(new_key())  # an unregistered key of the same shape: caught by shape
    assert other not in redact(f"key={other}")
    assert redact("DESK_SENDER_KEY=abc") == REDACTED


# ---- migration 0005 downgrade refuses with encrypted rows ---------------------------------


def test_downgrade_refuses_while_rows_are_encrypted(pg_url):
    from alembic import command
    from alembic.config import Config

    admin_url = os.environ["DESK_TEST_DATABASE_URL"]
    name = f"desk_b02_{uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = admin_url.rsplit("/", 1)[0] + f"/{name}"
    engine = create_engine(url)
    try:
        cfg = Config(os.path.join(REPO_ROOT, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(REPO_ROOT, "alembic"))
        cfg.attributes["url"] = url
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO invites (id, bound_sender, channel, business_phone_id, "
                    "created_at, expires_at, state) VALUES (gen_random_uuid(), :v, "
                    "'whatsapp', '1', now(), now(), 'open')"
                ),
                {"v": "enc1:" + "A" * 50},
            )
        with pytest.raises(RuntimeError, match="refusing to downgrade 0005.*invites.bound_sender"):
            command.downgrade(cfg, "0004")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0005"
            assert conn.execute(text("SELECT count(*) FROM invites")).scalar() == 1
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM invites"))
        command.downgrade(cfg, "0004")
        command.upgrade(cfg, "head")
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
