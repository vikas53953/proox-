"""Queueing report parts and failure notices into the outbox (idempotent keys)."""

import hashlib
import uuid
from datetime import date, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from desk.db.models import Outbox, Tenant
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
    body = kw["body"]
    return {
        "id": uuid.uuid4(),
        "state": "PENDING",
        "attempts": 0,
        "payload_hash": hashlib.sha256(body.encode()).hexdigest(),
        **kw,
    }


def enqueue_report(
    session: Session, tenant: Tenant, report: Report, parts: list[str], now: datetime
) -> None:
    for i, body in enumerate(parts, 1):
        session.execute(
            pg_insert(Outbox)
            .values(
                **_row(
                    idempotency_key=f"report:{report.id}:v{report.version}:{tenant.id}:p{i}",
                    tenant_id=tenant.id,
                    business_phone_id=tenant.business_phone_id,
                    recipient=tenant.sender,
                    kind="report_part",
                    body=body,
                    created_at=now,
                    proactive=True,
                    report_id=report.id,
                    report_version=report.version,
                    trading_date=report.trading_date,
                    part_no=i,
                    part_total=len(parts),
                )
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )


def enqueue_failure_notice(
    session: Session, tenant: Tenant, day: date, job_kind: str, category: str, now: datetime
) -> None:
    body = FAILURE_NOTICE.format(day=f"{day:%d %b %Y}", reason=FAILURE_REASONS[category])
    session.execute(
        pg_insert(Outbox)
        .values(
            **_row(
                idempotency_key=f"failure:{job_kind}:{day.isoformat()}:{tenant.id}",
                tenant_id=tenant.id,
                business_phone_id=tenant.business_phone_id,
                recipient=tenant.sender,
                kind="failure_notice",
                body=body,
                created_at=now,
                proactive=True,
                trading_date=day,
            )
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
