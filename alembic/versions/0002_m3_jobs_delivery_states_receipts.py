"""M3 jobs, delivery states, receipts

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hard_stop_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_epoch", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("budget_seconds", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('PENDING', 'LEASED', 'DONE', 'FAILED', 'CANCELLED', 'SKIPPED')",
            name="job_state",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "kind",
            "trading_date",
            "tenant_id",
            name="job_once_per_day",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        "delivery_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("outbox_id", sa.Uuid(), nullable=True),
        sa.Column("provider_message_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=16), nullable=True),
        sa.Column("error_title", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["outbox_id"],
            ["outbox.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_message_id", "status", name="receipt_once"),
    )
    op.create_index(
        op.f("ix_delivery_receipts_outbox_id"), "delivery_receipts", ["outbox_id"], unique=False
    )
    op.add_column(
        "outbox", sa.Column("proactive", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("outbox", sa.Column("report_id", sa.String(length=64), nullable=True))
    op.add_column("outbox", sa.Column("report_version", sa.Integer(), nullable=True))
    op.add_column("outbox", sa.Column("trading_date", sa.Date(), nullable=True))
    op.add_column("outbox", sa.Column("part_no", sa.Integer(), nullable=True))
    op.add_column("outbox", sa.Column("part_total", sa.Integer(), nullable=True))
    op.add_column("outbox", sa.Column("payload_hash", sa.String(length=64), nullable=True))
    op.add_column("outbox", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("outbox", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("outbox", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("outbox", sa.Column("provider_message_id", sa.String(length=128), nullable=True))
    op.add_column("outbox", sa.Column("last_status_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("outbox", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("outbox", sa.Column("wait_reason", sa.Text(), nullable=True))
    op.create_unique_constraint("outbox_provider_message_id_key", "outbox", ["provider_message_id"])
    # Autogenerate does not emit CHECK constraints on existing tables; added by hand.
    op.create_check_constraint(
        "outbox_state",
        "outbox",
        "state IN ('PENDING', 'SENDING', 'ACCEPTED', 'SENT', 'DELIVERED', 'READ', 'FAILED', "
        "'UNKNOWN', 'WAITING_WINDOW', 'CANCELLED')",
    )


def downgrade() -> None:
    op.drop_constraint("outbox_state", "outbox", type_="check")
    op.drop_constraint("outbox_provider_message_id_key", "outbox", type_="unique")
    op.drop_column("outbox", "wait_reason")
    op.drop_column("outbox", "error")
    op.drop_column("outbox", "last_status_at")
    op.drop_column("outbox", "provider_message_id")
    op.drop_column("outbox", "claimed_at")
    op.drop_column("outbox", "next_attempt_at")
    op.drop_column("outbox", "attempts")
    op.drop_column("outbox", "payload_hash")
    op.drop_column("outbox", "part_total")
    op.drop_column("outbox", "part_no")
    op.drop_column("outbox", "trading_date")
    op.drop_column("outbox", "report_version")
    op.drop_column("outbox", "report_id")
    op.drop_column("outbox", "proactive")
    op.drop_index(op.f("ix_delivery_receipts_outbox_id"), table_name="delivery_receipts")
    op.drop_table("delivery_receipts")
    op.drop_table("jobs")
