"""Add durable pending-action confirmation lifetime.

Revision ID: 20260814_0002
Revises: 20260814_0001
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0002"
down_revision: str | None = "20260814_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pending_actions",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pending_actions",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE pending_actions "
        "SET created_at = CURRENT_TIMESTAMP, expires_at = CURRENT_TIMESTAMP "
        "WHERE created_at IS NULL OR expires_at IS NULL"
    )
    op.alter_column("pending_actions", "created_at", nullable=False)
    op.alter_column("pending_actions", "expires_at", nullable=False)
    op.create_index(
        "ix_pending_actions_expires_at",
        "pending_actions",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_pending_actions_expires_at", table_name="pending_actions")
    op.drop_column("pending_actions", "expires_at")
    op.drop_column("pending_actions", "created_at")
