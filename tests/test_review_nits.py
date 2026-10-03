"""Two nits from the M8T security review.

a. logsafe: a bare Telegram token ending in "-" is redacted whole (the old trailing `\\b`
   left the final "-" behind).
b. outbox claim: a cooling LATER part never holds back an EARLIER part of the same
   recipient/endpoint; a cooling earlier part still holds later ones (order kept)."""

import uuid
from datetime import timedelta

from desk.db.models import Outbox
from desk.logsafe import REDACTED, redact
from desk.outbox.sender import claim
from tests.delivery_helpers import ist

BOT_ID_PREFIX = "1234567890:"


def test_bare_token_ending_in_dash_is_redacted_whole():
    for tail in ("-", "--", "_-", "-A-"):
        token = BOT_ID_PREFIX + "A" * 34 + tail
        assert redact(token) == REDACTED, tail
        assert redact(f"token {token}, next") == f"token {REDACTED}, next"
    assert redact(f"x={BOT_ID_PREFIX}{'b' * 35}") == f"x={REDACTED}"  # plain token unchanged


def _part(s, part_no: int, *, cooling_until=None, recipient="919800000001", created=None):
    row = Outbox(
        id=uuid.uuid4(),
        idempotency_key=f"rp:{uuid.uuid4().hex}",
        channel="whatsapp",
        business_phone_id="100200300",
        recipient=recipient,
        kind="report_part",
        body=f"part {part_no}",
        state="PENDING",
        created_at=created or ist(8, 0),
        part_no=part_no,
        part_total=3,
        attempts=1 if cooling_until else 0,
        next_attempt_at=cooling_until,
    )
    s.add(row)
    return row.id


def test_cooling_later_part_does_not_hold_back_an_earlier_part(db):
    now = ist(8, 5)
    with db() as s:
        p1 = _part(s, 1)
        _part(s, 2, cooling_until=now + timedelta(minutes=1))  # later part retrying
        s.commit()
    with db() as s:
        assert claim(s, now, 10) == [p1]
        s.rollback()


def test_cooling_earlier_part_still_holds_later_parts(db):
    now = ist(8, 5)
    with db() as s:
        _part(s, 1, cooling_until=now + timedelta(minutes=1))
        _part(s, 2)
        _part(s, 3)
        _part(s, 1, recipient="919800000002")  # another recipient is not held
        later_msg = _part(s, 0, created=ist(8, 1))  # a later message: held too
        s.commit()
    with db() as s:
        claimed = claim(s, now, 10)
        assert len(claimed) == 1 and later_msg not in claimed
        assert s.get(Outbox, claimed[0]).recipient == "919800000002"
        s.rollback()


def test_rows_tied_in_claim_order_still_hold_each_other(db):
    """Same created_at and no part number: no defined order, so a cooling one holds."""
    now = ist(8, 5)
    with db() as s:
        a = Outbox(
            idempotency_key="neutral:a",
            business_phone_id="100200300",
            recipient="919800000001",
            kind="neutral",
            body="a",
            created_at=ist(8, 0),
            next_attempt_at=now + timedelta(minutes=1),
        )
        b = Outbox(
            idempotency_key="neutral:b",
            business_phone_id="100200300",
            recipient="919800000001",
            kind="neutral",
            body="b",
            created_at=ist(8, 0),
        )
        s.add_all([a, b])
        s.commit()
    with db() as s:
        assert claim(s, now, 10) == []
        s.rollback()
