"""Add durable customer, AI, and agent ticket messages.

Revision ID: 20260825_0003
Revises: 20260814_0002
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_0003"
down_revision: str | None = "20260814_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ticket_messages",
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("ticket_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("sender_role", sa.String(length=32), nullable=False),
        sa.Column("sender_id", sa.String(length=128), nullable=False),
        sa.Column("body", sa.String(length=8000), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index("ix_ticket_messages_ticket_id", "ticket_messages", ["ticket_id"], unique=False)
    op.create_index(
        "ix_ticket_messages_customer_id",
        "ticket_messages",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_ticket_messages_sender_role",
        "ticket_messages",
        ["sender_role"],
        unique=False,
    )
    op.create_index(
        "ix_ticket_messages_sender_id",
        "ticket_messages",
        ["sender_id"],
        unique=False,
    )
    op.create_index(
        "ix_ticket_messages_created_at",
        "ticket_messages",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ticket_messages_conversation_id",
        "ticket_messages",
        ["conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_messages_conversation_id", table_name="ticket_messages")
    op.drop_index("ix_ticket_messages_created_at", table_name="ticket_messages")
    op.drop_index("ix_ticket_messages_sender_id", table_name="ticket_messages")
    op.drop_index("ix_ticket_messages_sender_role", table_name="ticket_messages")
    op.drop_index("ix_ticket_messages_customer_id", table_name="ticket_messages")
    op.drop_index("ix_ticket_messages_ticket_id", table_name="ticket_messages")
    op.drop_table("ticket_messages")
