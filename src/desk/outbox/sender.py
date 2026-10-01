"""Outbox sender: claim -> decide (policy) -> send -> record the outcome.

The claim is committed (state SENDING) BEFORE the network call. If the process dies
mid-send, the row stays SENDING; the sweep later marks it UNKNOWN — it may already have
been sent, so it is never resent blindly (RC10). Only a status receipt reconciles it.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from desk.db.models import Outbox, Tenant
from desk.outbox.policy import TEMPLATE_KIND, Action, decide
from desk.transport.whatsapp.client import (
    GraphClient,
    Outcome,
    SendResult,
    template_payload,
    text_payload,
)
from desk.transport.whatsapp.templates import TemplateRegistry

SENDING_GRACE = timedelta(minutes=5)
MAX_SEND_ATTEMPTS = 5


def backoff(attempts: int) -> timedelta:
    return min(timedelta(seconds=30 * 2 ** max(attempts - 1, 0)), timedelta(minutes=10))


@dataclass
class SendStats:
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1


def sweep_stuck(session: Session, now: datetime) -> int:
    res = session.execute(
        update(Outbox)
        .where(Outbox.state == "SENDING", Outbox.claimed_at < now - SENDING_GRACE)
        .values(
            state="UNKNOWN",
            last_status_at=now,
            error="sender stopped mid-send; may have been sent; not resent",
        )
    )
    return res.rowcount


def claim(session: Session, now: datetime, limit: int) -> list[uuid.UUID]:
    rows = (
        session.execute(
            select(Outbox)
            .where(
                Outbox.state == "PENDING",
                (Outbox.next_attempt_at.is_(None)) | (Outbox.next_attempt_at <= now),
            )
            .order_by(Outbox.created_at, Outbox.part_no)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.state, row.claimed_at, row.attempts = "SENDING", now, row.attempts + 1
    return [r.id for r in rows]


def _record(row: Outbox, outcome: Outcome, now: datetime) -> str:
    row.last_status_at = now
    match outcome.result:
        case SendResult.ACCEPTED:
            row.state, row.provider_message_id, row.error = (
                "ACCEPTED",
                outcome.provider_message_id,
                None,
            )
        case SendResult.RETRY if row.attempts < MAX_SEND_ATTEMPTS:
            row.state, row.next_attempt_at = "PENDING", now + backoff(row.attempts)
            row.error = f"retry later: {outcome.detail}"
        case SendResult.RETRY:
            row.state, row.error = "FAILED", f"gave up after {row.attempts}: {outcome.detail}"
        case SendResult.WINDOW_CLOSED:
            row.state, row.wait_reason = "WAITING_WINDOW", f"provider: {outcome.detail}"
        case SendResult.FAILED:
            row.state, row.error = "FAILED", outcome.detail
        case SendResult.UNKNOWN:
            row.state, row.error = "UNKNOWN", f"not resent: {outcome.detail}"
    return row.state


def _queue_template(session: Session, row: Outbox, now: datetime) -> None:
    session.execute(
        pg_insert(Outbox)
        .values(
            id=uuid.uuid4(),
            idempotency_key=f"tpl:{row.report_id}:v{row.report_version}:{row.tenant_id}",
            tenant_id=row.tenant_id,
            business_phone_id=row.business_phone_id,
            recipient=row.recipient,
            kind=TEMPLATE_KIND,
            body="(template report_ready)",
            state="PENDING",
            created_at=now,
            proactive=True,
            report_id=row.report_id,
            report_version=row.report_version,
            trading_date=row.trading_date,
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )


def send_batch(
    factory: sessionmaker[Session],
    client: GraphClient,
    templates: TemplateRegistry,
    now: datetime,
    limit: int = 50,
) -> SendStats:
    stats = SendStats()
    with factory() as s:
        for _ in range(sweep_stuck(s, now)):
            stats.add("UNKNOWN(swept)")
        ids = claim(s, now, limit)
        s.commit()
    for row_id in ids:
        with factory() as s:
            row = s.get(Outbox, row_id)
            tenant = s.get(Tenant, row.tenant_id) if row.tenant_id else None
            decision = decide(s, row, tenant, now, templates)
            if decision.action is Action.CANCEL:
                row.state, row.error, row.last_status_at = "CANCELLED", decision.reason, now
            elif decision.action is Action.WAIT:
                row.state, row.wait_reason, row.last_status_at = (
                    "WAITING_WINDOW",
                    decision.reason,
                    now,
                )
                if decision.request_template:
                    _queue_template(s, row, now)
            s.commit()
            if decision.action in (Action.CANCEL, Action.WAIT):
                stats.add(row.state)
                continue
            if decision.action is Action.SEND_TEMPLATE:
                t = decision.template
                payload = template_payload(
                    row.recipient, t.name, t.language, [f"{row.trading_date:%d %b %Y}"], str(row.id)
                )
            else:
                payload = text_payload(row.recipient, row.body, str(row.id))
        outcome = client.send(payload)  # network call outside any open transaction
        with factory() as s:
            row = s.get(Outbox, row_id, with_for_update=True)
            if row.state != "SENDING":  # a receipt already reconciled it meanwhile
                stats.add(row.state)
                continue
            stats.add(_record(row, outcome, now))
            s.commit()
    return stats
