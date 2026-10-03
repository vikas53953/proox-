"""Review fix: the bind replies (welcome, privacy notice, opt-in) have no part_no, and
claim orders by (created_at, part_no). They used to share created_at, so their order was
whatever PostgreSQL returned for a tie. Now they are 1 us apart: the order holds even when
both the uuids and the physical row order are reversed."""

import uuid
from datetime import timedelta

from sqlalchemy import select, text

from desk.db.models import Outbox
from desk.onboarding.invites import create_invite
from desk.onboarding.service import ONBOARDING_STEP
from desk.outbox.sender import claim
from desk.transport.telegram.client import FakeTelegram
from desk.transport.telegram.poller import poll_once
from tests.delivery_helpers import ist
from tests.test_telegram_privacy_notice import ON

CHATS = [5550101 + i for i in range(6)]
ORDER = ["welcome", "privacy_notice", "opt_in"]


def bind_all(db):
    fake = FakeTelegram(bot_id=ON.bot_id)
    _, client = fake.transport(ON.bot_token)
    for chat in CHATS:
        with db() as s:
            _, code = create_invite(
                s,
                business_phone_id=ON.bot_id,
                now=ist(7, 0),
                ttl=timedelta(hours=24),
                channel="telegram",
            )
            s.commit()
        fake.user_says(chat, f"/start {code}")
    poll_once(db, client, ON, ist(7, 5))


def reverse_storage(db) -> None:
    """Re-insert every outbox row in reverse claim order with descending uuids, so
    neither the uuid nor the heap order happens to give the right answer."""
    with db() as s:
        s.execute(text("CREATE TEMP TABLE ob AS SELECT * FROM outbox"))
        q = "SELECT id FROM ob ORDER BY created_at DESC, kind"  # opt_in first on a tie
        rows = list(s.execute(text(q)).scalars())
        ids = sorted((uuid.uuid4() for _ in rows), reverse=True)
        for old, new in zip(rows, ids, strict=True):
            s.execute(text("UPDATE ob SET id = :n WHERE id = :o"), {"n": new, "o": old})
        s.execute(text("DELETE FROM outbox"))
        s.execute(text("INSERT INTO outbox SELECT * FROM ob ORDER BY created_at DESC, kind"))
        s.execute(text("DROP TABLE ob"))
        s.commit()


def test_bind_replies_are_strictly_ordered_by_created_at(db):
    bind_all(db)
    with db() as s:
        rows = s.execute(select(Outbox)).scalars().all()
    by_chat: dict[str, dict[str, Outbox]] = {}
    for r in rows:
        by_chat.setdefault(r.recipient, {})[r.kind] = r
    assert len(by_chat) == len(CHATS)
    for kinds in by_chat.values():
        assert list(kinds) and set(kinds) == set(ORDER)
        times = [kinds[k].created_at for k in ORDER]
        assert times[1] - times[0] == ONBOARDING_STEP and times[2] - times[1] == ONBOARDING_STEP
        assert all(kinds[k].part_no is None for k in ORDER)


def test_claim_returns_welcome_privacy_opt_in_even_with_reversed_storage(db):
    bind_all(db)
    reverse_storage(db)
    for limit in (len(CHATS) * 3, 1):  # one batch, and one row at a time
        claimed = []
        with db() as s:
            while True:
                ids = claim(s, ist(7, 6), limit)
                if not ids:
                    break
                claimed += ids
            kinds = {
                r.id: (r.recipient, r.kind)
                for r in s.execute(select(Outbox).where(Outbox.id.in_(claimed))).scalars()
            }
            s.rollback()  # unclaim for the next pass
        assert len(claimed) == len(CHATS) * 3
        per_chat: dict[str, list[str]] = {}
        for i in claimed:
            chat, kind = kinds[i]
            per_chat.setdefault(chat, []).append(kind)
        assert all(seq == ORDER for seq in per_chat.values()), per_chat


def test_text_all_parts_are_spaced_in_order():
    """TEXT ALL parts get strictly increasing created_at, so claim order is their order."""
    import inspect

    from desk.onboarding import service

    src = inspect.getsource(service)
    assert '"text_part", body, at,' in src and "(i - 1) * ONBOARDING_STEP" in src
