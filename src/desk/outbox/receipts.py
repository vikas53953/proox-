"""Status webhooks -> delivery state (RC11). States only move forward:
ACCEPTED < SENT < DELIVERED < READ; FAILED unless already delivered/read.
UNKNOWN rows are reconciled through biz_opaque_callback_data (our outbox id), which the
Cloud API echoes in status webhooks. A read receipt says nothing about report validity.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from desk.db.models import DeliveryReceipt, Outbox
from desk.transport.whatsapp.payload import StatusUpdate

STATUS_MAP = {"sent": "SENT", "delivered": "DELIVERED", "read": "READ", "failed": "FAILED"}
RANK = {"UNKNOWN": 0, "SENDING": 0, "ACCEPTED": 1, "SENT": 2, "DELIVERED": 3, "READ": 4}


def _find(session: Session, st: StatusUpdate) -> Outbox | None:
    row = session.execute(
        select(Outbox).where(Outbox.provider_message_id == st.provider_message_id).with_for_update()
    ).scalar_one_or_none()
    if row is None and st.callback:
        try:
            row = session.get(Outbox, uuid.UUID(st.callback), with_for_update=True)
        except ValueError:
            return None
        if row is not None and (
            row.recipient != st.recipient
            or (row.provider_message_id not in (None, st.provider_message_id))
        ):
            return None  # callback must match the same recipient and message
    return row


def apply_status(session: Session, st: StatusUpdate, now: datetime) -> str:
    new = STATUS_MAP.get(st.status)
    if new is None:
        return "ignored_status"
    row = _find(session, st)
    stored = session.execute(
        pg_insert(DeliveryReceipt)
        .values(
            id=uuid.uuid4(),
            outbox_id=row.id if row else None,
            provider_message_id=st.provider_message_id,
            status=st.status,
            provider_time=st.provider_time,
            received_at=now,
            error_code=st.error_code,
            error_title=st.error_title,
        )
        .on_conflict_do_nothing(constraint="receipt_once")
        .returning(DeliveryReceipt.id)
    ).first()
    if stored is None:
        return "duplicate_receipt"
    if row is None:
        return "orphan_receipt"
    if row.provider_message_id is None:
        row.provider_message_id = st.provider_message_id  # reconciles an UNKNOWN send
    if new == "FAILED":
        if RANK.get(row.state, 0) < RANK["DELIVERED"]:
            row.state = "FAILED"
            row.error = f"provider failed: {st.error_code} {st.error_title or ''}".strip()
    elif RANK[new] > RANK.get(row.state, -1):
        row.state = new
    row.last_status_at = now
    return row.state
