"""B06 review fix: after `senders rotate` the stored ids are under the NEW key. If the
operator leaves DESK_SENDER_KEY at the OLD key, `senders status` must say so and startup
must refuse (before: both only looked at the "enc1:" prefix and said "consistent").
Rotation also locks every covered table so a running writer cannot add old-key rows."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from desk import pii
from desk.app import create_app
from tests.delivery_helpers import add_tenant
from tests.test_b02_sender_encryption import ALICE, on, raw_values
from tests.test_b06_key_rotation import (
    key_forms,
    key_text,
    new_key,
    rotate,
    run_cli,
    seed,
)


@pytest.fixture(autouse=True)
def restore_cipher():
    yield
    pii.configure(False)


def status(monkeypatch, capsys, pg_url, key: bytes):
    env = {"DESK_ENCRYPT_SENDERS": "1", "DESK_SENDER_KEY": key_text(key)}
    return run_cli(monkeypatch, capsys, pg_url, env, "senders", "status")


def factory_refusal(monkeypatch, pg_url, key: bytes) -> str:
    """The shared CLI startup path (serve / invite / ...)."""
    from desk.__main__ import _factory

    monkeypatch.setenv("DESK_DATABASE_URL", pg_url)
    monkeypatch.setenv("DESK_ENCRYPT_SENDERS", "1")
    monkeypatch.setenv("DESK_SENDER_KEY", key_text(key))
    monkeypatch.delenv("DESK_SENDER_KEY_NEW", raising=False)
    with pytest.raises(SystemExit) as exc:
        _factory()
    return str(exc.value.code)


def test_rotate_then_old_key_left_is_flagged_and_refused(
    db, pg_url, monkeypatch, capsys, wa_settings, settings
):
    old, new = new_key(), new_key()
    seed(db, wa_settings, old)
    code, out = rotate(monkeypatch, capsys, pg_url, old, new)
    assert code == 0 and "consistent" in out  # checked against the NEW key after rotate

    # operator forgot the swap: DESK_SENDER_KEY still the OLD key
    code, out = status(monkeypatch, capsys, pg_url, old)
    assert code == 1, out
    assert "stored transport ids do not open with DESK_SENDER_KEY" in out
    assert "tenants.sender" in out and "consistent" not in out
    with pytest.raises(pii.SenderStateError, match="do not open with DESK_SENDER_KEY"):
        create_app(settings=on(settings, old), session_factory=db)
    msg = factory_refusal(monkeypatch, pg_url, old)
    assert msg.startswith("refusing to start") and "do not open with DESK_SENDER_KEY" in msg
    for k in (old, new):
        assert all(f not in out + msg for f in key_forms(k))
    assert ALICE not in out + msg
    stored = [v for vals in raw_values(db).values() for v in vals]
    assert stored and all(v not in out + msg for v in stored)

    # correct swap: consistent, startup works
    code, out = status(monkeypatch, capsys, pg_url, new)
    assert code == 0 and "flag ON; consistent" in out
    create_app(settings=on(settings, new), session_factory=db)


def test_one_value_under_another_key_is_caught(db, settings):
    """Sampling covers both ends of the column, so a single odd row is still caught."""
    good, stranger = new_key(), new_key()
    pii.configure(True, good)
    add_tenant(db, ALICE)
    pii.configure(True, stranger)
    add_tenant(db, "919800000099")
    with pytest.raises(pii.SenderStateError, match=r"tenants\.sender"):
        create_app(settings=on(settings, good), session_factory=db)


def test_flag_off_and_plain_rows_still_start(db, settings):
    add_tenant(db, ALICE)
    create_app(settings=settings, session_factory=db)


def test_rotate_locks_covered_tables_against_writers(db, wa_settings):
    old, new = new_key(), new_key()
    seed(db, wa_settings, old)
    with db() as rot, db() as other:
        pii.rotate_existing(rot, old, new)  # transaction still open (not committed)
        other.execute(text("SET lock_timeout = '300ms'"))
        assert other.execute(text("SELECT count(*) FROM tenants")).scalar() == 2  # reads ok
        other.rollback()
        first_col = {}
        for table, col in pii.COVERED:
            first_col.setdefault(table, col)
        for table, col in first_col.items():  # a no-op write still needs ROW EXCLUSIVE
            other.execute(text("SET lock_timeout = '300ms'"))
            with pytest.raises(OperationalError, match="lock timeout"):
                other.execute(text(f"UPDATE {table} SET {col} = {col} WHERE false"))  # noqa: S608
            other.rollback()
        rot.rollback()
