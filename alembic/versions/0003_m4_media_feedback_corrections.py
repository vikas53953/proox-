"""M4 media, feedback, corrections

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "corrections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.String(length=64), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("new_version", sa.Integer(), nullable=False),
        sa.Column("original_hash", sa.String(length=64), nullable=False),
        sa.Column("lens", sa.String(length=8), nullable=False),
        sa.Column("previous_claim", sa.Text(), nullable=False),
        sa.Column("corrected_claim", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scenario_impact", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "new_version", name="correction_version"),
    )
    op.create_table(
        "feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("report_id", sa.String(length=64), nullable=True),
        sa.Column("report_version", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("text_redacted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('useful', 'not_useful', 'text')", name="feedback_kind"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index(op.f("ix_feedback_tenant_id"), "feedback", ["tenant_id"], unique=False)
    op.create_table(
        "media",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.String(length=64), nullable=False),
        sa.Column("report_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("mime", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=128), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_media_tenant_id"), "media", ["tenant_id"], unique=False)
    op.add_column("outbox", sa.Column("media_id", sa.Uuid(), nullable=True))
    op.add_column("outbox", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("outbox_media_id_fkey", "outbox", "media", ["media_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("outbox_media_id_fkey", "outbox", type_="foreignkey")
    op.drop_column("outbox", "expires_at")
    op.drop_column("outbox", "media_id")
    op.drop_index(op.f("ix_media_tenant_id"), table_name="media")
    op.drop_table("media")
    op.drop_index(op.f("ix_feedback_tenant_id"), table_name="feedback")
    op.drop_table("feedback")
    op.drop_table("corrections")
