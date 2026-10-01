"""Evolve pending actions into durable local business operations.

Revision ID: 20260917_0005
Revises: 20260915_0004
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from math import isfinite
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0005"
down_revision: str | None = "20260915_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("order_id", sa.String(64)),
    ("quoted_amount", sa.Float()),
    ("snapshot", sa.JSON()),
    ("snapshot_hash", sa.String(64)),
    ("snapshot_version", sa.Integer()),
    ("version", sa.Integer()),
    ("legacy", sa.Boolean()),
    ("approval_required", sa.Boolean()),
    ("confirmed_by", sa.String(128)),
    ("confirmed_at", sa.DateTime(timezone=True)),
    ("confirmed_snapshot_hash", sa.String(64)),
    ("terminal_at", sa.DateTime(timezone=True)),
    ("ticket_id", sa.String(64)),
)
REQUIRED = {
    "snapshot",
    "snapshot_hash",
    "snapshot_version",
    "version",
    "legacy",
    "approval_required",
}


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column("pending_actions", sa.Column(name, type_, nullable=True))
    op.add_column("action_receipts", sa.Column("operation_id", sa.String(64), nullable=True))
    op.add_column("action_receipts", sa.Column("legacy", sa.Boolean(), nullable=True))
    op.add_column("audit_events", sa.Column("operation_id", sa.String(64), nullable=True))
    connection = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(
        connection, only=["orders", "pending_actions", "action_receipts", "audit_events"]
    )
    orders, actions, receipts, audit = [
        metadata.tables[t] for t in ["orders", "pending_actions", "action_receipts", "audit_events"]
    ]
    connection.execute(receipts.update().values(legacy=True))
    now = datetime.now(UTC)
    for row in connection.execute(sa.select(actions)).mappings().all():
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        reference = payload.get("order_id")
        order = (
            connection.execute(
                sa.select(orders.c.order_id).where(
                    orders.c.tenant_id == row["tenant_id"], orders.c.order_id == reference
                )
            ).first()
            if isinstance(reference, str)
            else None
        )
        order_id = reference if order else None
        amount = payload.get("amount")
        amount = (
            float(amount)
            if type(amount) in (float, int) and isfinite(amount) and amount > 0
            else None
        )
        receipt = (
            connection.execute(
                sa.select(receipts).where(
                    receipts.c.tenant_id == row["tenant_id"],
                    receipts.c.idempotency_key == row["action_id"],
                    receipts.c.action_kind == row["kind"],
                )
            )
            .mappings()
            .first()
        )
        matches = (
            receipt is not None
            and isinstance(receipt["result"], dict)
            and (receipt["result"].get("order_id") == reference)
        )
        if row["kind"] == "refund":
            matches = matches and amount is not None and receipt["result"].get("amount") == amount
        if row["kind"] == "refund":
            matches = matches and receipt[("result")].get("refund_status") in {
                ("requested"),
                ("already_requested"),
            }
        elif row["kind"] == "return_request":
            matches = matches and receipt["result"].get("return_status") == "requested"
        else:
            matches = False
        previous = row["status"]
        if previous == "executed" and matches and order_id is not None:
            status, result = "submitted", dict(receipt["result"])
        elif previous in {"cancelled", "expired"}:
            status, result = previous, row["result"] or {previous: True}
        else:
            status, result = "invalidated", {"reason": "legacy_proposal_requires_reprepare"}
        snapshot = {
            "source": "legacy",
            "approval_requirement": "unknown",
            "tenant_id": row["tenant_id"],
            "customer_id": row["customer_id"],
            "order_id": order_id,
            "kind": row["kind"],
            "prepared_amount": amount,
        }
        digest = hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        connection.execute(
            actions.update()
            .where(actions.c.tenant_id == row["tenant_id"], actions.c.action_id == row["action_id"])
            .values(
                order_id=order_id,
                quoted_amount=amount,
                snapshot=snapshot,
                snapshot_hash=digest,
                snapshot_version=0,
                version=0,
                legacy=True,
                approval_required=False,
                status=status,
                result=result,
                terminal_at=now if status == "invalidated" else None,
            )
        )
        if matches:
            connection.execute(
                receipts.update()
                .where(
                    receipts.c.tenant_id == row["tenant_id"],
                    receipts.c.idempotency_key == row["action_id"],
                )
                .values(operation_id=row["action_id"])
            )
        connection.execute(
            audit.insert().values(
                event_id="AUD-" + uuid4().hex[:12].upper(),
                tenant_id=row["tenant_id"],
                trace_id="MIG-20260917-0005",
                actor_id="system:migration",
                operation="operation.legacy_mapped",
                resource_type="business_operation",
                resource_id=row["action_id"],
                operation_id=row["action_id"],
                outcome=status,
                details={"previous_status": previous, "legacy": True},
                created_at=now,
            )
        )
    for name, type_ in COLUMNS:
        if name in REQUIRED:
            op.alter_column("pending_actions", name, existing_type=type_, nullable=False)
    op.alter_column("action_receipts", "legacy", existing_type=sa.Boolean(), nullable=False)

    # Frozen DDL follows; it intentionally does not import mutable application metadata.
    _create_constraints()
    op.create_index(
        "uq_operation_business_intent",
        "pending_actions",
        ["tenant_id", "customer_id", "order_id", "kind"],
        unique=True,
        postgresql_where=sa.text(
            "NOT legacy AND status IN ('pending_confirmation','pending_approval','submitted')"
        ),
    )
    op.create_table(
        "operation_approvals",
        sa.Column("operation_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("operation_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "operation_id"],
            ["pending_actions.tenant_id", "pending_actions.action_id"],
            name="fk_approval_operation",
        ),
        sa.CheckConstraint("decision IN ('approve','reject')", name="ck_approval_decision"),
        sa.CheckConstraint(
            "operation_version >= 1 AND snapshot_version >= 1", name="ck_approval_version"
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if (
        connection.execute(
            sa.text("SELECT 1 FROM pending_actions WHERE NOT legacy LIMIT 1")
        ).first()
        is not None
    ):
        raise RuntimeError("operation_downgrade_requires_no_new_operations")
    op.drop_table("operation_approvals")
    op.drop_index("uq_operation_business_intent", table_name="pending_actions")
    _drop_constraints()
    op.execute(
        "UPDATE pending_actions SET status = CASE "
        "WHEN status = 'submitted' THEN 'executed' "
        "WHEN status = 'cancelled' THEN 'cancelled' ELSE 'expired' END"
    )
    op.drop_column("audit_events", "operation_id")
    op.drop_column("action_receipts", "operation_id")
    op.drop_column("action_receipts", "legacy")
    for name, _ in reversed(COLUMNS):
        op.drop_column("pending_actions", name)


def _create_constraints() -> None:
    op.create_check_constraint(
        "ck_operation_legacy_terminal",
        "pending_actions",
        "NOT legacy OR status NOT IN ('pending_confirmation','pending_approval')",
    )
    op.create_unique_constraint("uq_order_tenant_id", "orders", ["tenant_id", "order_id"])
    op.create_unique_constraint("uq_ticket_tenant_id", "tickets", ["tenant_id", "ticket_id"])
    op.create_unique_constraint(
        "uq_operation_tenant_id", "pending_actions", ["tenant_id", "action_id"]
    )
    op.create_unique_constraint(
        "uq_operation_tenant_id_kind", "pending_actions", ["tenant_id", "action_id", "kind"]
    )
    op.create_check_constraint(
        "ck_operation_status",
        "pending_actions",
        (
            "status IN ('pending_confirmation','pending_approval','submitted','cancelled'"
            ",'expired','rejected','invalidated')"
        ),
    )
    op.create_check_constraint(
        "ck_operation_identity",
        "pending_actions",
        (
            "version >= 0 AND snapshot_version >= 0 AND (legacy OR (version >= 1 AND snap"
            "shot_version >= 1 AND order_id IS NOT NULL AND kind IN ('refund','return_req"
            "uest')))"
        ),
    )
    op.create_check_constraint(
        "ck_operation_quote",
        "pending_actions",
        "legacy OR kind != 'refund' OR (quoted_amount IS NOT NULL AND quoted_amount > 0)",
    )
    op.create_check_constraint(
        "ck_operation_confirmation",
        "pending_actions",
        (
            "legacy OR ((confirmed_at IS NULL AND confirmed_by IS NULL AND confirmed_snap"
            "shot_hash IS NULL) OR (confirmed_at IS NOT NULL AND confirmed_by IS NOT NULL"
            " AND confirmed_snapshot_hash IS NOT NULL AND confirmed_snapshot_hash = snaps"
            "hot_hash))"
        ),
    )
    op.create_check_constraint(
        "ck_operation_submission",
        "pending_actions",
        (
            "legacy OR status != 'submitted' OR (confirmed_at IS NOT NULL AND result IS N"
            "OT NULL AND terminal_at IS NOT NULL)"
        ),
    )
    op.create_foreign_key(
        "fk_operation_order",
        "pending_actions",
        "orders",
        ["tenant_id", "order_id"],
        ["tenant_id", "order_id"],
    )
    op.create_foreign_key(
        "fk_operation_ticket",
        "pending_actions",
        "tickets",
        ["tenant_id", "ticket_id"],
        ["tenant_id", "ticket_id"],
    )
    op.create_foreign_key(
        "fk_receipt_operation",
        "action_receipts",
        "pending_actions",
        ["tenant_id", "operation_id", "action_kind"],
        ["tenant_id", "action_id", "kind"],
    )
    op.create_unique_constraint(
        "uq_receipt_operation", "action_receipts", ["tenant_id", "operation_id"]
    )
    op.create_check_constraint(
        "ck_receipt_identity",
        "action_receipts",
        "legacy OR (operation_id IS NOT NULL AND operation_id = idempotency_key)",
    )
    op.create_foreign_key(
        "fk_audit_operation",
        "audit_events",
        "pending_actions",
        ["tenant_id", "operation_id"],
        ["tenant_id", "action_id"],
    )


def _drop_constraints() -> None:
    op.drop_constraint("ck_operation_legacy_terminal", "pending_actions", type_="check")
    op.drop_constraint("fk_audit_operation", "audit_events", type_="foreignkey")
    op.drop_constraint("ck_receipt_identity", "action_receipts", type_="check")
    op.drop_constraint("uq_receipt_operation", "action_receipts", type_="unique")
    op.drop_constraint("fk_receipt_operation", "action_receipts", type_="foreignkey")
    op.drop_constraint("fk_operation_ticket", "pending_actions", type_="foreignkey")
    op.drop_constraint("fk_operation_order", "pending_actions", type_="foreignkey")
    op.drop_constraint("ck_operation_submission", "pending_actions", type_="check")
    op.drop_constraint("ck_operation_confirmation", "pending_actions", type_="check")
    op.drop_constraint("ck_operation_quote", "pending_actions", type_="check")
    op.drop_constraint("ck_operation_identity", "pending_actions", type_="check")
    op.drop_constraint("ck_operation_status", "pending_actions", type_="check")
    op.drop_constraint("uq_operation_tenant_id_kind", "pending_actions", type_="unique")
    op.drop_constraint("uq_operation_tenant_id", "pending_actions", type_="unique")
    op.drop_constraint("uq_ticket_tenant_id", "tickets", type_="unique")
    op.drop_constraint("uq_order_tenant_id", "orders", type_="unique")
