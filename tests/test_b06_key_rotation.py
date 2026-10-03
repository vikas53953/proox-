"""B06: rotate the B02 transport-id key with `python -m desk senders rotate`.

Old key from DESK_SENDER_KEY, new key from DESK_SENDER_KEY_NEW. Every covered value is
re-encrypted in ONE transaction; a re-run skips values already under the new key; a value
neither key opens aborts with nothing changed; keys never reach the output or logs."""

import base64
import os
import sys

import pytest
from sqlalchemy import select

from desk import pii
from desk.db.models import Invite, Outbox, Tenant
from desk.logsafe import redact
from tests.delivery_helpers import add_tenant, ist
from tests.test_b02_sender_encryption import ALICE, BOB, make_invite, raw_dump, raw_values

CAROL = "919800000003"


def new_key() -> bytes:
    return os.urandom(pii.KEY_BYTES)


def key_text(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode().rstrip("=")


def key_forms(key: bytes) -> list[str]:
    padded = base64.urlsafe_b64encode(key).decode()
    std = base64.b64encode(key).decode()
    return [padded, padded.rstrip("="), std, std.rstrip("=")]


@pytest.fixture(autouse=True)
def restore_cipher():
    yield
    pii.configure(False)


def run_cli(monkeypatch, capsys, pg_url, env: dict, *argv) -> tuple[int, str]:
    from desk.__main__ import main

    for k in ("DESK_ENCRYPT_SENDERS", "DESK_SENDER_KEY", "DESK_SENDER_KEY_NEW"):
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


def rotate(monkeypatch, capsys, pg_url, old: bytes, new: bytes | None, flag="1"):
    env = {"DESK_ENCRYPT_SENDERS": flag, "DESK_SENDER_KEY": key_text(old)}
    if new is not None:
        env["DESK_SENDER_KEY_NEW"] = key_text(new)
    return run_cli(monkeypatch, capsys, pg_url, env, "senders", "rotate")


def seed(db, wa_settings, key: bytes):
    """Rows in several covered columns, all encrypted under `key`."""
    pii.configure(True, key)
    tid = add_tenant(db, ALICE)
    add_tenant(db, CAROL)
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
    pii.configure(False)
    return tid


def lookups_work(db, key: bytes, tid) -> None:
    pii.configure(True, key)
    with db() as s:
        assert s.execute(select(Tenant).where(Tenant.sender == ALICE)).scalar_one().id == tid
        assert s.execute(select(Invite).where(Invite.bound_sender == BOB)).scalar_one()
        row = s.execute(select(Outbox)).scalar_one()
        assert (row.recipient, row.in_reply_to) == (ALICE, "wamid.OLD1")
    pii.configure(False)


def test_rotate_round_trip_and_idempotent_rerun(db, pg_url, monkeypatch, capsys, wa_settings):
    old, new = new_key(), new_key()
    tid = seed(db, wa_settings, old)
    before = raw_values(db)

    code, out = rotate(monkeypatch, capsys, pg_url, old, new)
    assert code == 0, out
    assert "rotated 6 values; 0 already under the new key" in out
    assert "consistent" in out
    after = raw_values(db)
    for col, vals in before.items():
        assert len(after[col]) == len(vals), col
        assert all(v.startswith("enc1:") for v in after[col]), col
        assert not set(vals) & set(after[col]), col  # every stored value changed
    assert ALICE not in raw_dump(db) and BOB not in raw_dump(db)
    lookups_work(db, new, tid)  # app lookups work under the new key
    pii.configure(True, old)  # the old key no longer opens anything
    with db() as s, pytest.raises(pii.SenderKeyError):
        s.execute(select(Tenant)).scalars().all()
    pii.configure(False)

    # re-run with the same env (operator has not swapped yet): nothing changes
    code, out = rotate(monkeypatch, capsys, pg_url, old, new)
    assert code == 0 and "rotated 0 values;" in out and "already under the new key" in out
    assert raw_values(db) == after
    # after the swap, startup with the new key alone is consistent
    code, out = run_cli(
        monkeypatch,
        capsys,
        pg_url,
        {"DESK_ENCRYPT_SENDERS": "1", "DESK_SENDER_KEY": key_text(new)},
        "senders",
        "status",
    )
    assert code == 0 and "consistent" in out


def test_partial_state_rotates_the_rest(db, pg_url, monkeypatch, capsys, wa_settings):
    """Some values already under the new key (e.g. written after a manual swap): those are
    skipped, the rest rotated."""
    old, new = new_key(), new_key()
    tid = seed(db, wa_settings, old)
    pii.configure(True, new)
    add_tenant(db, "919800000077")
    pii.configure(False)
    code, out = rotate(monkeypatch, capsys, pg_url, old, new)
    assert code == 0 and "rotated 6 values; 1 already under the new key" in out
    lookups_work(db, new, tid)


def test_value_under_an_unknown_key_aborts_with_no_change(
    db, pg_url, monkeypatch, capsys, wa_settings
):
    old, new, stranger = new_key(), new_key(), new_key()
    seed(db, wa_settings, old)  # tenants/invites first in COVERED: would rotate first
    pii.configure(True, stranger)
    with db() as s:  # a later table (outbox) holds a value neither key opens
        s.add(
            Outbox(
                idempotency_key="neutral:x",
                business_phone_id="100200300",
                recipient="919800000055",
                kind="neutral",
                body="x",
                created_at=ist(7, 1),
            )
        )
        s.commit()
    pii.configure(False)
    before = raw_values(db)
    code, out = rotate(monkeypatch, capsys, pg_url, old, new)
    assert code == 2 and "refusing" in out and "nothing was changed" in out
    assert "outbox.recipient" in out
    assert raw_values(db) == before  # atomic: earlier tables were rolled back too
    assert "919800000055" not in out
    for k in (old, new, stranger):
        assert all(f not in out for f in key_forms(k))


def test_wrong_old_key_aborts_with_no_change(db, pg_url, monkeypatch, capsys, wa_settings):
    real, wrong, new = new_key(), new_key(), new_key()
    seed(db, wa_settings, real)
    before = raw_values(db)
    code, out = rotate(monkeypatch, capsys, pg_url, wrong, new)
    assert code == 2 and "refusing" in out and "nothing was changed" in out
    assert raw_values(db) == before


def test_refusals(db, pg_url, monkeypatch, capsys, wa_settings):
    old, new = new_key(), new_key()
    seed(db, wa_settings, old)
    before = raw_values(db)
    code, out = rotate(monkeypatch, capsys, pg_url, old, new, flag="0")
    assert code == 2 and "DESK_ENCRYPT_SENDERS=1" in out
    code, out = rotate(monkeypatch, capsys, pg_url, old, None)
    assert code == 2 and "DESK_SENDER_KEY_NEW (not set)" in out
    code, out = rotate(monkeypatch, capsys, pg_url, old, old)
    assert code == 2 and "must differ" in out
    env = {
        "DESK_ENCRYPT_SENDERS": "1",
        "DESK_SENDER_KEY": key_text(old),
        "DESK_SENDER_KEY_NEW": "c2hvcnQ",
    }
    code, out = run_cli(monkeypatch, capsys, pg_url, env, "senders", "rotate")
    assert code == 2 and "DESK_SENDER_KEY_NEW must decode to exactly 64 bytes" in out
    assert raw_values(db) == before
    pii.configure(False)  # the CLI configured the process; write this row in plain text
    add_tenant(db, "919800000066")  # a plaintext row left: rotate refuses
    code, out = rotate(monkeypatch, capsys, pg_url, old, new)
    assert code == 2 and "senders encrypt" in out and "tenants.sender" in out
    assert "919800000066" not in out
    with db() as s:
        assert {r.sender for r in s.execute(select(Tenant)).scalars()} >= {"919800000066"}
    for k in (old, new):
        assert all(f not in out for f in key_forms(k))


def test_keys_never_printed_or_logged(db, pg_url, monkeypatch, capsys, wa_settings):
    old, new = new_key(), new_key()
    seed(db, wa_settings, old)
    code, out = rotate(monkeypatch, capsys, pg_url, old, new)
    assert code == 0
    for k in (old, new):
        assert all(f not in out for f in key_forms(k))
        for f in key_forms(k):  # both keys registered with logsafe by exact value
            assert f not in redact(f"value {f} here")
    assert redact(f"DESK_SENDER_KEY_NEW={key_text(new)}") == "[REDACTED]"
    assert redact("DESK_SENDER_KEY_NEW=abc") == "[REDACTED]"
