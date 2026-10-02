"""Add trusted tenant ownership; legacy/demo backfill requires explicit opt-in.

Revision ID: 20260915_0004
Revises: 20260825_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "20260915_0004"
down_revision: str | None = "20260825_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "orders",
    "tickets",
    "ticket_events",
    "ticket_messages",
    "pending_actions",
    "action_receipts",
    "audit_events",
)
# Compatibility assignment for explicitly identified synthetic data, NOT recovered provenance.
LEGACY_DEMO_TENANT = "TENANT-DEMO"
INDEXES = (
    ("orders", "ix_orders_tenant_customer", ["tenant_id", "customer_id"]),
    ("tickets", "ix_tickets_tenant_status_created", ["tenant_id", "status", "created_at"]),
    (
        "tickets",
        "ix_tickets_tenant_customer_conversation",
        ["tenant_id", "customer_id", "conversation_id", "created_at"],
    ),
    (
        "ticket_messages",
        "ix_ticket_messages_tenant_ticket_created",
        ["tenant_id", "ticket_id", "created_at"],
    ),
    (
        "pending_actions",
        "ix_pending_actions_tenant_status_expiry",
        ["tenant_id", "status", "expires_at"],
    ),
    (
        "audit_events",
        "ix_audit_events_tenant_trace_created",
        ["tenant_id", "trace_id", "created_at"],
    ),
)


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("tenant_migration_requires_online_provenance_check")
    connection = op.get_bind()
    has_legacy_rows = any(
        connection.execute(sa.select(sa.literal(1)).select_from(sa.table(table)).limit(1)).first()
        is not None
        for table in TABLES
    )
    if has_legacy_rows and (
        context.get_x_argument(as_dictionary=True).get("legacy_demo_backfill") != "true"
    ):
        raise RuntimeError(
            "legacy_tenant_mapping_required: synthetic/demo data needs explicit "
            "-x legacy_demo_backfill=true; real data needs an authoritative migration mapping"
        )
    for table in TABLES:
        op.add_column(table, sa.Column("tenant_id", sa.String(64), nullable=True))
        legacy = sa.table(table, sa.column("tenant_id", sa.String(64)))
        op.execute(legacy.update().values(tenant_id=LEGACY_DEMO_TENANT))
        op.alter_column(table, "tenant_id", existing_type=sa.String(64), nullable=False)
    for table, name, columns in INDEXES:
        op.create_index(name, table, columns, unique=False)


def downgrade() -> None:
    for table, name, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
    for table in reversed(TABLES):
        op.drop_column(table, "tenant_id")
