"""Queueing report parts, addenda, corrections and failure notices (idempotent keys)."""

import hashlib
import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from desk.db.models import Media, Outbox, Tenant
from desk.outbox.parts import Part
from desk.report.model import Report

# User-facing reasons are fixed phrases: internal error text never reaches a customer.
FAILURE_REASONS = {
    "feed": "market data feed se data nahi mila",
    "review": "report quality check pass nahi hua",
    "deadline": "report time par (09:15 IST se pehle) ready nahi hua",
    "budget": "report apne time/cost budget mein nahi bana",
    "internal": "system error aaya",
}
FAILURE_NOTICE = (
    "Aaj ({day}) ka pre-market brief nahi bana: {reason}. Koi current report claim nahi "
    "kar rahe; purana report aaj ka bata kar nahi bhejenge. Agla step: abhi tay nahi (koi ETA "
    "nahi)."
)


def _row(**kw) -> dict:
    payload = kw["body"].encode() + (kw.pop("_media_bytes", b"") or b"")
    return {
        "id": uuid.uuid4(),
        "state": "PENDING",
        "attempts": 0,
        "payload_hash": hashlib.sha256(payload).hexdigest(),
        **kw,
    }


def _store_media(
    session: Session, tenant: Tenant, report: Report, part: Part, now: datetime
) -> uuid.UUID:
    m = part.media
    row = Media(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        report_id=report.id,
        report_version=report.version,
        kind=m.kind,
        mime=m.mime,
        filename=m.filename,
        sha256=hashlib.sha256(m.content).hexdigest(),
        content=m.content,
        caption=part.body,
        manifest=m.manifest,
        created_at=now,
    )
    session.add(row)
    session.flush()
    return row.id


def enqueue_report(
    session: Session, tenant: Tenant, report: Report, parts: list[Part], now: datetime
) -> None:
    for i, part in enumerate(parts, 1):
        key = f"report:{report.id}:v{report.version}:{tenant.id}:p{i}"
        if session.execute(select(Outbox.id).where(Outbox.idempotency_key == key)).first():
            continue  # already queued (media is not stored twice either)
        media_id = _store_media(session, tenant, report, part, now) if part.media else None
        session.execute(
            pg_insert(Outbox)
            .values(
                **_row(
                    idempotency_key=key,
                    tenant_id=tenant.id,
                    business_phone_id=tenant.business_phone_id,
                    recipient=tenant.sender,
                    channel=tenant.channel,
                    kind="report_part",
                    body=part.body,
                    created_at=now,
                    proactive=True,
                    report_id=report.id,
                    report_version=report.version,
                    trading_date=report.trading_date,
                    part_no=i,
                    part_total=len(parts),
                    media_id=media_id,
                    _media_bytes=part.media.content if part.media else None,
                )
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )


def enqueue_message(
    session: Session,
    tenant: Tenant,
    *,
    key: str,
    kind: str,
    body: str,
    now: datetime,
    trading_date: date | None = None,
    report: Report | None = None,
    expires_at: datetime | None = None,
    proactive: bool = True,
    in_reply_to: str | None = None,
) -> None:
    session.execute(
        pg_insert(Outbox)
        .values(
            **_row(
                idempotency_key=key,
                tenant_id=tenant.id,
                business_phone_id=tenant.business_phone_id,
                recipient=tenant.sender,
                channel=tenant.channel,
                kind=kind,
                body=body,
                created_at=now,
                proactive=proactive,
                trading_date=trading_date,
                report_id=report.id if report else None,
                report_version=report.version if report else None,
                expires_at=expires_at,
                in_reply_to=in_reply_to,
            )
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )


def enqueue_failure_notice(
    session: Session, tenant: Tenant, day: date, job_kind: str, category: str, now: datetime
) -> None:
    body = FAILURE_NOTICE.format(day=f"{day:%d %b %Y}", reason=FAILURE_REASONS[category])
    enqueue_message(
        session,
        tenant,
        key=f"failure:{job_kind}:{day.isoformat()}:{tenant.id}",
        kind="failure_notice",
        body=body,
        now=now,
        trading_date=day,
    )
