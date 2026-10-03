"""Minimal feedback and verified corrections (J09). No journal, no P&L (v1.3 scope).

Feedback: "USEFUL" / "NOT USEFUL" / "FEEDBACK <text>" in chat, linked to the tenant's
latest report. Corrections: issued by the operator; the original report row is never
changed — the correction names report, versions, section, previous claim, verified
change, source/time and scenario impact, and goes only to tenants who were sent the
original.
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from desk.core.lens import LENS_TITLES, LensId
from desk.db.models import Correction, Feedback, Outbox, StoredReport, Tenant
from desk.market_calendar import fmt_ist
from desk.onboarding.invites import redact
from desk.outbox.notices import enqueue_message

SENT_STATES = ("ACCEPTED", "SENT", "DELIVERED", "READ", "UNKNOWN")
FEEDBACK_ACK = "Shukriya, feedback note kar liya."


def parse_feedback(text: str | None) -> tuple[str, str | None] | None:
    words = (text or "").strip()
    upper = words.upper()
    if upper in ("USEFUL", "\U0001f44d"):
        return "useful", None
    if upper in ("NOT USEFUL", "\U0001f44e"):
        return "not_useful", None
    if upper.startswith("FEEDBACK"):
        return "text", redact(words[len("FEEDBACK") :].strip(" :-"))[:2000]
    return None


def record_feedback(
    session: Session, tenant: Tenant, message_id: str, kind: str, text: str | None, now: datetime
) -> None:
    latest = session.execute(
        select(StoredReport)
        .where(StoredReport.tenant_id == tenant.id)
        .order_by(StoredReport.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    session.execute(
        pg_insert(Feedback)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            message_id=message_id,
            report_id=latest.report_id if latest else None,
            report_version=latest.version if latest else None,
            kind=kind,
            text_redacted=text,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["message_id"])
    )


class CorrectionError(ValueError):
    pass


def issue_correction(
    session: Session,
    *,
    report_id: str,
    from_version: int,
    lens: LensId,
    previous_claim: str,
    corrected_claim: str,
    source_url: str,
    source_time: datetime,
    scenario_impact: str,
    reason: str,
    now: datetime,
) -> Correction:
    original = session.execute(
        select(StoredReport)
        .where(StoredReport.report_id == report_id, StoredReport.version == from_version)
        .limit(1)
    ).scalar_one_or_none()
    if original is None:
        raise CorrectionError("no such delivered report/version")
    if not source_url.startswith("https://"):
        raise CorrectionError("a correction needs a verifiable https source")
    latest = (
        session.execute(
            select(func.max(Correction.new_version)).where(Correction.report_id == report_id)
        ).scalar()
        or from_version
    )
    corr = Correction(
        id=uuid.uuid4(),
        report_id=report_id,
        trading_date=original.trading_date,
        from_version=from_version,
        new_version=latest + 1,
        original_hash=original.content_hash,
        lens=lens.value,
        previous_claim=previous_claim,
        corrected_claim=corrected_claim,
        source_url=source_url,
        source_time=source_time,
        scenario_impact=scenario_impact,
        reason=reason,
        created_at=now,
    )
    session.add(corr)
    session.flush()
    body = (
        f"CORRECTION | {report_id} v{from_version} -> v{corr.new_version} | "
        f"{original.trading_date:%d %b %Y} | section {lens.value} {LENS_TITLES[lens]}\n"
        f"Previous claim: {previous_claim}\n"
        f"Verified change: {corrected_claim}\n"
        f"Source: {source_url} (as of {fmt_ist(source_time)})\n"
        f"Scenario impact: {scenario_impact}\n"
        f"The original v{from_version} stays on record unchanged."
    )
    recipients = (
        session.execute(
            select(Tenant)
            .join(Outbox, Outbox.tenant_id == Tenant.id)
            .where(
                Outbox.report_id == report_id,
                Outbox.report_version == from_version,
                Outbox.state.in_(SENT_STATES),
            )
            .distinct()
        )
        .scalars()
        .all()
    )
    for tenant in recipients:
        enqueue_message(
            session,
            tenant,
            key=f"correction:{corr.id}:{tenant.id}",
            kind="correction",
            body=body,
            now=now,
            trading_date=original.trading_date,
        )
    return corr
