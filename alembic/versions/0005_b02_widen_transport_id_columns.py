"""B02 widen transport-id columns for encrypted values

Revision ID: 0005
Revises: 0004

Widening only: stored values are not changed (plaintext rows stay plaintext; the operator
encrypts them with `python -m desk senders encrypt`). Downgrade refuses while any value
is encrypted, rather than truncating ciphertext.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# (table, column, old length, new length, nullable)
COLUMNS = (
    ("tenants", "sender", 20, 64, False),
    ("invites", "bound_sender", 20, 64, True),
    ("inbound_messages", "sender", 20, 64, False),
    ("inbound_messages", "message_id", 128, 256, False),
    ("outbox", "recipient", 20, 64, False),
    ("outbox", "idempotency_key", 200, 320, False),
    ("outbox", "in_reply_to", 128, 256, True),
    ("outbox", "provider_message_id", 128, 256, True),
    ("delivery_receipts", "provider_message_id", 128, 256, False),
    ("feedback", "message_id", 128, 256, False),
)


def upgrade() -> None:
    for table, col, old, new, nullable in COLUMNS:
        op.alter_column(
            table,
            col,
            existing_type=sa.String(length=old),
            type_=sa.String(length=new),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    conn = op.get_bind()
    encrypted = [
        f"{table}.{col}"
        for table, col, *_ in COLUMNS
        if conn.execute(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE {col} LIKE 'enc1:%')")  # noqa: S608
        ).scalar()
    ]
    if encrypted:
        raise RuntimeError(
            "refusing to downgrade 0005: encrypted transport ids are stored in "
            + ", ".join(encrypted)
            + " (they do not fit the old column sizes; nothing was changed)"
        )
    for table, col, old, new, nullable in COLUMNS:
        op.alter_column(
            table,
            col,
            existing_type=sa.String(length=new),
            type_=sa.String(length=old),
            existing_nullable=nullable,
        )
