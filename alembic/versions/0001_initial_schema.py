"""initial schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("business_phone_id", sa.String(length=32), nullable=False),
        sa.Column("sender", sa.String(length=20), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("opt_in_state", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("pending_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "opt_in_state IN ('unasked', 'asked', 'yes', 'no', 'stopped')",
            name="tenant_opt_in_state",
        ),
        sa.CheckConstraint("state IN ('pending', 'ready')", name="tenant_state"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_phone_id", "sender", name="tenant_identity"),
    )
    op.create_table(
        "agent_bindings",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("chief_identity", sa.String(length=64), nullable=False),
        sa.Column("research_identity", sa.String(length=64), nullable=False),
        sa.Column("reviewer_identity", sa.String(length=64), nullable=False),
        sa.Column("vm_ids", sa.JSON(), nullable=True),
        sa.Column("capacity", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.create_table(
        "invites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("bound_sender", sa.String(length=20), nullable=True),
        sa.Column("business_phone_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("consumed_by", sa.Uuid(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('open', 'consumed', 'revoked')", name="invite_state"),
        sa.CheckConstraint(
            "token_hash IS NOT NULL OR bound_sender IS NOT NULL", name="invite_has_code_or_sender"
        ),
        sa.ForeignKeyConstraint(
            ["consumed_by"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_invites_bound_sender"), "invites", ["bound_sender"], unique=False)
    op.create_table(
        "message_bodies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("body_redacted", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_message_bodies_tenant_id"), "message_bodies", ["tenant_id"], unique=False
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("business_phone_id", sa.String(length=32), nullable=False),
        sa.Column("recipient", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("in_reply_to", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(op.f("ix_outbox_recipient"), "outbox", ["recipient"], unique=False)
    op.create_index(op.f("ix_outbox_tenant_id"), "outbox", ["tenant_id"], unique=False)
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.String(length=64), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "report_id", "version", name="report_version_per_tenant"),
    )
    op.create_index(op.f("ix_reports_tenant_id"), "reports", ["tenant_id"], unique=False)
    op.create_table(
        "inbound_messages",
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("business_phone_id", sa.String(length=32), nullable=False),
        sa.Column("sender", sa.String(length=20), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("provider_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("safe_content_ref", sa.Uuid(), nullable=True),
        sa.Column("handled_as", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["safe_content_ref"],
            ["message_bodies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index(
        op.f("ix_inbound_messages_sender"), "inbound_messages", ["sender"], unique=False
    )
    op.create_index(
        op.f("ix_inbound_messages_tenant_id"), "inbound_messages", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_inbound_messages_tenant_id"), table_name="inbound_messages")
    op.drop_index(op.f("ix_inbound_messages_sender"), table_name="inbound_messages")
    op.drop_table("inbound_messages")
    op.drop_index(op.f("ix_reports_tenant_id"), table_name="reports")
    op.drop_table("reports")
    op.drop_index(op.f("ix_outbox_tenant_id"), table_name="outbox")
    op.drop_index(op.f("ix_outbox_recipient"), table_name="outbox")
    op.drop_table("outbox")
    op.drop_index(op.f("ix_message_bodies_tenant_id"), table_name="message_bodies")
    op.drop_table("message_bodies")
    op.drop_index(op.f("ix_invites_bound_sender"), table_name="invites")
    op.drop_table("invites")
    op.drop_table("agent_bindings")
    op.drop_table("tenants")
