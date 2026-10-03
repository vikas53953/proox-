"""Tenant-scoped data access (RC08). Every read goes through a TenantScope.

Asking for another tenant's row gives exactly the same "not found" as a row that does
not exist, so the answer never reveals whether someone else's data exists.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from desk.db.models import Inbound, MessageBody, Outbox, StoredReport
from desk.report.model import Report


class NotFoundError(LookupError):
    pass


class TenantScope:
    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._s = session
        self.tenant_id = tenant_id

    def save_report(self, report: Report, now: datetime) -> StoredReport:
        row = StoredReport(
            tenant_id=self.tenant_id,
            report_id=report.id,
            trading_date=report.trading_date,
            version=report.version,
            content_hash=report.content_hash,
            body=report.model_dump(mode="json"),
            created_at=now,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def report(self, row_id: uuid.UUID) -> StoredReport:
        row = self._s.execute(
            select(StoredReport).where(
                StoredReport.id == row_id, StoredReport.tenant_id == self.tenant_id
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("report not found")
        return row

    def reports(self) -> list[StoredReport]:
        return list(
            self._s.execute(
                select(StoredReport).where(StoredReport.tenant_id == self.tenant_id)
            ).scalars()
        )

    def outbox(self) -> list[Outbox]:
        return list(
            self._s.execute(
                select(Outbox).where(Outbox.tenant_id == self.tenant_id).order_by(Outbox.created_at)
            ).scalars()
        )

    def inbound(self) -> list[Inbound]:
        return list(
            self._s.execute(select(Inbound).where(Inbound.tenant_id == self.tenant_id)).scalars()
        )

    def message_body(self, ref: uuid.UUID) -> MessageBody:
        row = self._s.execute(
            select(MessageBody).where(
                MessageBody.id == ref, MessageBody.tenant_id == self.tenant_id
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("message not found")
        return row
