"""M8T channel column, transport cursors

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transport_cursors",
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("business_id", sa.String(length=32), nullable=False),
        sa.Column("last_update_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("channel", "business_id"),
    )
    op.add_column(
        "inbound_messages",
        sa.Column("channel", sa.String(length=16), server_default="whatsapp", nullable=False),
    )
    op.add_column(
        "invites",
        sa.Column("channel", sa.String(length=16), server_default="whatsapp", nullable=False),
    )
    op.add_column(
        "outbox",
        sa.Column("channel", sa.String(length=16), server_default="whatsapp", nullable=False),
    )
    op.add_column(
        "tenants",
        sa.Column("channel", sa.String(length=16), server_default="whatsapp", nullable=False),
    )
    op.drop_constraint(op.f("tenant_identity"), "tenants", type_="unique")
    op.create_unique_constraint(
        "tenant_identity", "tenants", ["channel", "business_phone_id", "sender"]
    )


def downgrade() -> None:
    # Without the channel column, Telegram rows would look like WhatsApp rows (older code
    # would even hand queued Telegram messages to the WhatsApp client). Refuse instead.
    bind = op.get_bind()
    for table in ("tenants", "invites", "outbox", "inbound_messages"):
        found = bind.execute(
            sa.text(f"SELECT 1 FROM {table} WHERE channel <> 'whatsapp' LIMIT 1")  # noqa: S608
        ).first()
        if found:
            raise RuntimeError(
                f"cannot downgrade 0004: {table} has non-WhatsApp rows; remove them first"
            )
    op.drop_constraint("tenant_identity", "tenants", type_="unique")
    op.create_unique_constraint(
        op.f("tenant_identity"),
        "tenants",
        ["business_phone_id", "sender"],
        postgresql_nulls_not_distinct=False,
    )
    op.drop_column("tenants", "channel")
    op.drop_column("outbox", "channel")
    op.drop_column("invites", "channel")
    op.drop_column("inbound_messages", "channel")
    op.drop_table("transport_cursors")
