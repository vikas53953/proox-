"""Owner decision 2026-10-04: Telegram is locked by default. With nothing set,
TELEGRAM_ALLOWED_CHAT_IDS lets no chat in ("*" opens it), TELEGRAM_REQUIRE_PREBIND is on
and TELEGRAM_TRACK_MEMBER_UPDATES is on. Each can still be set explicitly."""

import pytest

from desk.config import load_settings
from tests.test_telegram_prebind import ME, invites, run_invite


def test_empty_environment_is_locked_prebound_and_tracking():
    tg = load_settings({}).telegram
    assert tg.allowed_chat_ids == frozenset()
    assert not tg.allows(str(ME)) and not tg.allows("-100")
    assert tg.require_prebind is True and tg.track_member_updates is True


def test_explicit_values_still_win():
    tg = load_settings(
        {
            "TELEGRAM_ALLOWED_CHAT_IDS": "*",
            "TELEGRAM_REQUIRE_PREBIND": "0",
            "TELEGRAM_TRACK_MEMBER_UPDATES": "0",
        }
    ).telegram
    assert tg.allowed_chat_ids is None and tg.allows("anything")
    assert tg.require_prebind is False and tg.track_member_updates is False


@pytest.mark.parametrize("raw", ["**", "*,1", "* 1"])
def test_star_must_stand_alone(raw):
    from desk.config import SettingsError

    with pytest.raises(SettingsError, match="TELEGRAM_ALLOWED_CHAT_IDS"):
        load_settings({"TELEGRAM_ALLOWED_CHAT_IDS": raw})


def test_cli_invite_needs_chat_id_by_default(db, pg_url, monkeypatch, capsys):
    code, out = run_invite(monkeypatch, capsys, pg_url, {}, "--channel", "telegram")
    assert code != 0 and "TELEGRAM_REQUIRE_PREBIND" in out
    assert invites(db) == []


def test_cli_invite_notes_a_chat_off_the_allowlist(db, pg_url, monkeypatch, capsys):
    args = ("--channel", "telegram", "--chat-id", str(ME))
    code, out = run_invite(monkeypatch, capsys, pg_url, {}, *args)
    assert code == 0 and "not on TELEGRAM_ALLOWED_CHAT_IDS" in out
    env = {"TELEGRAM_ALLOWED_CHAT_IDS": str(ME)}
    code, out = run_invite(monkeypatch, capsys, pg_url, env, *args)
    assert code == 0 and "not on TELEGRAM_ALLOWED_CHAT_IDS" not in out
    assert len(invites(db)) == 2
