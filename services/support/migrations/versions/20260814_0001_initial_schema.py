"""Create initial SupportOps operational schema.

Revision ID: 20260814_0001
Revises: None
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("refundable_amount", sa.Float(), nullable=False),
        sa.Column("refund_status", sa.String(length=32), nullable=False),
        sa.Column("return_status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("order_id"),
    )
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"], unique=False)

    op.create_table(
        "tickets",
        sa.Column("ticket_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=32), nullable=False),
        sa.Column("assignee_id", sa.String(length=128), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ticket_id"),
    )
    op.create_index("ix_tickets_assignee_id", "tickets", ["assignee_id"], unique=False)
    op.create_index("ix_tickets_conversation_id", "tickets", ["conversation_id"], unique=False)
    op.create_index("ix_tickets_customer_id", "tickets", ["customer_id"], unique=False)
    op.create_index("ix_tickets_priority", "tickets", ["priority"], unique=False)
    op.create_index("ix_tickets_status", "tickets", ["status"], unique=False)

    op.create_table(
        "ticket_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("ticket_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_ticket_events_actor_id", "ticket_events", ["actor_id"], unique=False)
    op.create_index("ix_ticket_events_ticket_id", "ticket_events", ["ticket_id"], unique=False)

    op.create_table(
        "pending_actions",
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("action_id"),
    )
    op.create_index(
        "ix_pending_actions_conversation_id",
        "pending_actions",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_pending_actions_customer_id",
        "pending_actions",
        ["customer_id"],
        unique=False,
    )

    op.create_table(
        "action_receipts",
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("action_kind", sa.String(length=32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("idempotency_key"),
    )

    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"], unique=False)
    op.create_index("ix_audit_events_operation", "audit_events", ["operation"], unique=False)
    op.create_index("ix_audit_events_outcome", "audit_events", ["outcome"], unique=False)
    op.create_index("ix_audit_events_trace_id", "audit_events", ["trace_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_events_trace_id", table_name="audit_events")
    op.drop_index("ix_audit_events_outcome", table_name="audit_events")
    op.drop_index("ix_audit_events_operation", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("action_receipts")
    op.drop_index("ix_pending_actions_customer_id", table_name="pending_actions")
    op.drop_index("ix_pending_actions_conversation_id", table_name="pending_actions")
    op.drop_table("pending_actions")
    op.drop_index("ix_ticket_events_ticket_id", table_name="ticket_events")
    op.drop_index("ix_ticket_events_actor_id", table_name="ticket_events")
    op.drop_table("ticket_events")
    op.drop_index("ix_tickets_status", table_name="tickets")
    op.drop_index("ix_tickets_priority", table_name="tickets")
    op.drop_index("ix_tickets_customer_id", table_name="tickets")
    op.drop_index("ix_tickets_conversation_id", table_name="tickets")
    op.drop_index("ix_tickets_assignee_id", table_name="tickets")
    op.drop_table("tickets")
    op.drop_index("ix_orders_customer_id", table_name="orders")
    op.drop_table("orders")
