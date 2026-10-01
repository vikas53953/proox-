"""Record contracts from Technical spec v1.3 (Invite, Tenant, AgentBinding, Inbound,
Outbox, StoredReport). Times are stored in UTC.

Privacy (RC08): invite codes are stored only as a SHA-256 hash; inbound text is stored
with invite codes redacted; every tenant-owned row carries tenant_id.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

UTC_TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class Invite(Base):
    __tablename__ = "invites"
    __table_args__ = (
        CheckConstraint(
            "token_hash IS NOT NULL OR bound_sender IS NOT NULL", name="invite_has_code_or_sender"
        ),
        CheckConstraint("state IN ('open', 'consumed', 'revoked')", name="invite_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    bound_sender: Mapped[str | None] = mapped_column(String(20), index=True)
    channel: Mapped[str] = mapped_column(String(16), default="whatsapp", server_default="whatsapp")
    business_phone_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTC_TS)
    expires_at: Mapped[datetime] = mapped_column(UTC_TS)
    state: Mapped[str] = mapped_column(String(16), default="open")
    consumed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"))
    consumed_at: Mapped[datetime | None] = mapped_column(UTC_TS)


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("channel", "business_phone_id", "sender", name="tenant_identity"),
        CheckConstraint(
            "opt_in_state IN ('unasked', 'asked', 'yes', 'no', 'stopped')",
            name="tenant_opt_in_state",
        ),
        CheckConstraint("state IN ('pending', 'ready')", name="tenant_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # channel + business endpoint (WhatsApp phone_number_id / Telegram bot id) + sender id
    channel: Mapped[str] = mapped_column(String(16), default="whatsapp", server_default="whatsapp")
    business_phone_id: Mapped[str] = mapped_column(String(32))
    sender: Mapped[str] = mapped_column(String(20))  # transport identity only (wa_id / tg id)
    language: Mapped[str] = mapped_column(String(32), default="hinglish-roman")
    opt_in_state: Mapped[str] = mapped_column(String(16), default="unasked")
    state: Mapped[str] = mapped_column(String(16), default="pending")
    pending_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTC_TS)
    last_inbound_at: Mapped[datetime | None] = mapped_column(UTC_TS)

    @property
    def report_opt_in(self) -> bool:
        return self.opt_in_state == "yes"


class AgentBinding(Base):
    __tablename__ = "agent_bindings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    chief_identity: Mapped[str] = mapped_column(String(64))
    research_identity: Mapped[str] = mapped_column(String(64))
    reviewer_identity: Mapped[str] = mapped_column(String(64))
    vm_ids: Mapped[dict | None] = mapped_column(JSON)  # G05: no VMs allocated yet
    capacity: Mapped[str] = mapped_column(String(64))


class MessageBody(Base):
    __tablename__ = "message_bodies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    body_redacted: Mapped[str] = mapped_column(Text)


class Inbound(Base):
    __tablename__ = "inbound_messages"

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)  # dedupe key
    channel: Mapped[str] = mapped_column(String(16), default="whatsapp", server_default="whatsapp")
    business_phone_id: Mapped[str] = mapped_column(String(32))
    sender: Mapped[str] = mapped_column(String(20), index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    provider_time: Mapped[datetime] = mapped_column(UTC_TS)
    received_at: Mapped[datetime] = mapped_column(UTC_TS)
    type: Mapped[str] = mapped_column(String(32))
    safe_content_ref: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("message_bodies.id"))
    handled_as: Mapped[str] = mapped_column(String(32), default="received")


OUTBOX_STATES = (
    "PENDING",
    "SENDING",
    "ACCEPTED",
    "SENT",
    "DELIVERED",
    "READ",
    "FAILED",
    "UNKNOWN",
    "WAITING_WINDOW",
    "CANCELLED",
)


class Outbox(Base):
    """Every outbound message, its delivery state and why it is in that state (RC10/RC11).

    ACCEPTED = Graph API took it (has provider id); SENT/DELIVERED/READ/FAILED come only
    from receipts; UNKNOWN = we may have sent it but never got an answer (never resent
    blindly); WAITING_WINDOW = not allowed to send yet (24h window / template rules).
    """

    __tablename__ = "outbox"
    __table_args__ = (
        CheckConstraint(
            "state IN (" + ", ".join(f"'{x}'" for x in OUTBOX_STATES) + ")", name="outbox_state"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16), default="whatsapp", server_default="whatsapp")
    business_phone_id: Mapped[str] = mapped_column(String(32))
    recipient: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(UTC_TS)
    in_reply_to: Mapped[str | None] = mapped_column(String(128))
    proactive: Mapped[bool] = mapped_column(default=False, server_default=false())  # opt-in rules
    report_id: Mapped[str | None] = mapped_column(String(64))
    report_version: Mapped[int | None] = mapped_column(Integer)
    trading_date: Mapped[date | None] = mapped_column(Date)
    part_no: Mapped[int | None] = mapped_column(Integer)
    part_total: Mapped[int | None] = mapped_column(Integer)
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTC_TS)
    claimed_at: Mapped[datetime | None] = mapped_column(UTC_TS)
    provider_message_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    last_status_at: Mapped[datetime | None] = mapped_column(UTC_TS)
    error: Mapped[str | None] = mapped_column(Text)
    wait_reason: Mapped[str | None] = mapped_column(Text)
    media_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media.id"))
    expires_at: Mapped[datetime | None] = mapped_column(UTC_TS)  # e.g. addendum: 09:15 IST


class Media(Base):
    """Generated PDF / PNG, kept per tenant (blob store is G05; DB storage is scaffold-only).

    The caption carries report id / version / date / as-of; `manifest` records exactly
    which sourced inputs and renderer produced the file (D04)."""

    __tablename__ = "media"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    report_id: Mapped[str] = mapped_column(String(64))
    report_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # "document" | "image"
    mime: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(128))
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    caption: Mapped[str] = mapped_column(Text)
    manifest: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTC_TS)


class Feedback(Base):
    """Minimal feedback only: useful / not useful / free text. No journal (v1.3)."""

    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint("kind IN ('useful', 'not_useful', 'text')", name="feedback_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    message_id: Mapped[str] = mapped_column(String(128), unique=True)
    report_id: Mapped[str | None] = mapped_column(String(64))
    report_version: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    text_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTC_TS)


class Correction(Base):
    """A verified change to a delivered report. The original stays unchanged and
    identifiable; the correction names claim, source and versions (J09, RC10)."""

    __tablename__ = "corrections"
    __table_args__ = (UniqueConstraint("report_id", "new_version", name="correction_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    report_id: Mapped[str] = mapped_column(String(64))
    trading_date: Mapped[date] = mapped_column(Date)
    from_version: Mapped[int] = mapped_column(Integer)
    new_version: Mapped[int] = mapped_column(Integer)
    original_hash: Mapped[str] = mapped_column(String(64))
    lens: Mapped[str] = mapped_column(String(8))
    previous_claim: Mapped[str] = mapped_column(Text)
    corrected_claim: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    source_time: Mapped[datetime] = mapped_column(UTC_TS)
    scenario_impact: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTC_TS)


class TransportCursor(Base):
    """Long-polling position per channel/bot (Telegram getUpdates offset)."""

    __tablename__ = "transport_cursors"

    channel: Mapped[str] = mapped_column(String(16), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_update_id: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTC_TS)


class DeliveryReceipt(Base):
    """Every status webhook we receive, kept as evidence (duplicates collapse)."""

    __tablename__ = "delivery_receipts"
    __table_args__ = (UniqueConstraint("provider_message_id", "status", name="receipt_once"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    outbox_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("outbox.id"), index=True)
    provider_message_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    provider_time: Mapped[datetime] = mapped_column(UTC_TS)
    received_at: Mapped[datetime] = mapped_column(UTC_TS)
    error_code: Mapped[str | None] = mapped_column(String(16))
    error_title: Mapped[str | None] = mapped_column(Text)


JOB_STATES = ("PENDING", "LEASED", "DONE", "FAILED", "CANCELLED", "SKIPPED")


class Job(Base):
    """A dated unit of work with a durable lease (RC10).

    Fencing: every lease bumps lease_epoch; a worker may only finish the job with the
    epoch it leased, so a stale worker (lease expired, job cancelled) can never write.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint(
            "kind",
            "trading_date",
            "tenant_id",
            name="job_once_per_day",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "state IN (" + ", ".join(f"'{x}'" for x in JOB_STATES) + ")", name="job_state"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    trading_date: Mapped[date] = mapped_column(Date)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"))
    not_before: Mapped[datetime] = mapped_column(UTC_TS)
    deadline_at: Mapped[datetime] = mapped_column(UTC_TS)  # target, e.g. 08:45 IST
    hard_stop_at: Mapped[datetime] = mapped_column(UTC_TS)  # after this: give up, say so
    state: Mapped[str] = mapped_column(String(16), default="PENDING")
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(UTC_TS)
    lease_epoch: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    budget_seconds: Mapped[int] = mapped_column(Integer, default=600)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTC_TS)
    result: Mapped[dict | None] = mapped_column(JSON)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTC_TS)
    updated_at: Mapped[datetime] = mapped_column(UTC_TS)


class StoredReport(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("tenant_id", "report_id", "version", name="report_version_per_tenant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    report_id: Mapped[str] = mapped_column(String(64))
    trading_date: Mapped[date] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTC_TS)
