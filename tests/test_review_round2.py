"""Second review round: (1) a reply to a message from the same poll batch as /start is
claimed after the opt-in question; (2) the option chain never picks an expiry that has
already passed; (3) a UTF-8 BOM (Excel/Notepad saves) is handled the same way by the
adapter and by `nse verify`."""

import copy
import json
import shutil
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from desk.db.models import Outbox
from desk.feeds.base import DatasetKind
from desk.feeds.nse_public import FILES, FileSource, NseDataError, NsePublicFeed, parse_option_chain
from desk.feeds.nse_verify import verify_file
from desk.onboarding.invites import create_invite
from desk.outbox.sender import claim
from desk.transport.telegram.client import FakeTelegram
from desk.transport.telegram.poller import poll_once
from tests.delivery_helpers import ist
from tests.test_telegram_privacy_notice import ON

SAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "nse_public"
DAY_DIR = SAMPLES / "2026-10-01"


def _claimed_kinds(db, at) -> list[str]:
    with db() as s:
        ids = claim(s, at, 100)
        kinds = {r.id: r.kind for r in s.execute(select(Outbox)).scalars()}
        s.rollback()
    return [kinds[i] for i in ids]


def _bind_and_say(db, *texts: str) -> list[str]:
    fake = FakeTelegram(bot_id=ON.bot_id)
    _, client = fake.transport(ON.bot_token)
    with db() as s:
        _, code = create_invite(
            s,
            business_phone_id=ON.bot_id,
            now=ist(7, 0),
            ttl=timedelta(hours=24),
            channel="telegram",
        )
        s.commit()
    fake.user_says(777001, f"/start {code}")
    for t in texts:
        fake.user_says(777001, t)
    poll_once(db, client, ON, ist(7, 5))  # one batch, one shared `now`
    return _claimed_kinds(db, ist(7, 6))


def test_yes_in_the_same_batch_is_answered_after_the_opt_in_question(db):
    order = _bind_and_say(db, "YES")
    assert order == ["welcome", "privacy_notice", "opt_in", "opt_in_yes"], order


def test_a_question_in_the_same_batch_is_answered_after_onboarding(db):
    order = _bind_and_say(db, "what about nifty?", "STOP")
    assert order == ["welcome", "privacy_notice", "opt_in", "question_pending", "stop"], order


def test_replies_to_one_chat_have_strictly_increasing_created_at(db):
    _bind_and_say(db, "YES", "hello")
    with db() as s:
        times = list(s.execute(select(Outbox.created_at).order_by(Outbox.created_at)).scalars())
    assert len(times) == 5 and len(set(times)) == 5


def _chain_with_expiry(raw, expiry: str, timestamp: str):
    out = copy.deepcopy(raw)
    rec = out["records"]
    rec["timestamp"] = timestamp
    rec["expiryDates"] = [expiry, *rec["expiryDates"]]
    row = copy.deepcopy(rec["data"][0])
    row["expiryDate"] = expiry
    rec["data"].append(row)
    return out


@pytest.fixture
def chain():
    return json.loads(
        (DAY_DIR / FILES[DatasetKind.OPTION_CHAIN][0]).read_text(), parse_float=Decimal
    )


def test_option_chain_skips_an_expiry_that_closed_before_the_data_time(chain):
    raw = _chain_with_expiry(chain, "30-Sep-2026", "30-Sep-2026 15:30:00")
    _, _, meta = parse_option_chain(raw)
    assert meta["expiry"] == "2026-10-06"


def test_option_chain_keeps_todays_expiry_while_the_session_is_open(chain):
    raw = _chain_with_expiry(chain, "30-Sep-2026", "30-Sep-2026 11:00:00")
    _, _, meta = parse_option_chain(raw)
    assert meta["expiry"] == "2026-09-30"


def test_option_chain_with_only_past_expiries_is_an_error(chain):
    raw = copy.deepcopy(chain)
    raw["records"]["timestamp"] = "30-Dec-2026 10:00:00"
    with pytest.raises(NseDataError, match="every listed expiry has passed"):
        parse_option_chain(raw)


@pytest.fixture
def bom_samples(tmp_path) -> Path:
    root = tmp_path / "nse_public"
    shutil.copytree(SAMPLES, root)
    for kind in (DatasetKind.STOCKS, DatasetKind.SECTORS):
        path = root / "2026-10-01" / FILES[kind][0]
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    return root


@pytest.mark.parametrize("kind", [DatasetKind.STOCKS, DatasetKind.SECTORS])
def test_bom_files_parse_in_the_adapter_and_pass_verify(bom_samples, kind):
    from datetime import date

    feed = NsePublicFeed(FileSource(bom_samples))
    assert feed.fetch(kind, date(2026, 10, 1)) is not None, feed.problems()
    report = verify_file(bom_samples / "2026-10-01" / FILES[kind][0])
    assert report.ok, report
