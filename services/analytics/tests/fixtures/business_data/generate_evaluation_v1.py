"""Regenerate deterministic, synthetic Delivery v2 fixtures for analytics evaluation."""

import hashlib
import json
from pathlib import Path
from typing import Any


CONTRACT_SHA256 = "7c718f38934a9105d3529b32f24b62c08a1e86f7d9ea634668a35039aa427d2b"
CONTRACT_NAME = "supportops_business_data"
CONTRACT_VERSION = "v1"
DATASET_NAMES = ("orders_v1", "tickets_v1", "ticket_events_v1", "actions_v1")
GENERATED_AT = "2026-09-24T00:04:00Z"


def _rows(tenant_id: str) -> dict[str, list[dict[str, Any]]]:
    if tenant_id == "tenant-a":
        orders = [
            ("a-001", "customer-a-1", "paid", "12.30", "12.30", "none", "none"),
            ("a-002", "customer-a-2", "paid", "20.00", "20.00", "pending", "none"),
            ("a-003", "customer-a-3", "cancelled", "0.00", "0.00", "none", "none"),
            ("a-004", "customer-a-4", "pending", "99.90", "0.00", "none", "none"),
            ("a-005", "customer-a-5", "refunded", "14.00", "0.00", "approved", "none"),
            ("a-006", "customer-a-6", "paid", "100.00", "50.00", "none", "pending"),
        ]
        ticket_specs = (
            ("a-t-1", "customer-a-1", "conversation-a-1", "open", "high", "2026-09-20T00:00:00Z"),
            (
                "a-t-2", "customer-a-2", "conversation-a-2", "resolved", "urgent",
                "2026-09-21T00:00:00Z",
            ),
            (
                "a-t-3", "customer-a-3", "conversation-a-3", "pending_customer", "normal",
                "2026-09-22T00:00:00Z",
            ),
            ("a-t-4", "customer-a-4", "conversation-a-4", "closed", "low", "2026-09-23T00:00:00Z"),
        )
        event_specs = (
            ("a-e-1", "a-t-1", "open", "assigned", "2026-09-20T01:00:00Z"),
            ("a-e-2", "a-t-2", "open", "resolved", "2026-09-21T01:00:00Z"),
            ("a-e-3", "a-t-3", "assigned", "pending_customer", "2026-09-22T01:00:00Z"),
            ("a-e-4", "a-t-4", "resolved", "closed", "2026-09-23T01:00:00Z"),
        )
        action_specs = (
            ("a-act-1", "a-001", "a-t-1", "refund", "submitted", "12.30", True),
            ("a-act-2", "a-002", "a-t-2", "return_request", "pending_approval", None, True),
            ("a-act-3", "a-006", None, "refund", "pending_confirmation", "20.00", False),
            ("a-act-4", "a-005", "a-t-4", "refund", "rejected", "10.00", False),
        )
    elif tenant_id == "tenant-b":
        orders = [
            ("b-001", "customer-b-1", "paid", "500.00", "0.00", "none", "none"),
            ("b-002", "customer-b-2", "paid", "125.50", "25.50", "none", "pending"),
        ]
        ticket_specs = (
            ("b-t-1", "customer-b-1", "conversation-b-1", "closed", "low", "2026-09-23T00:00:00Z"),
        )
        event_specs = (("b-e-1", "b-t-1", "resolved", "closed", "2026-09-23T01:00:00Z"),)
        action_specs = (
            ("b-act-1", "b-002", "b-t-1", "return_request", "submitted", None, False),
        )
    else:
        raise ValueError("unknown synthetic tenant")

    orders_v1 = [
        {
            "tenant_id": tenant_id,
            "order_id": order_id,
            "customer_id": customer_id,
            "status": status,
            "amount": amount,
            "refundable_amount": refundable,
            "refund_status": refund_status,
            "return_status": return_status,
        }
        for (
            order_id,
            customer_id,
            status,
            amount,
            refundable,
            refund_status,
            return_status,
        ) in orders
    ]
    tickets_v1 = [
        {
            "tenant_id": tenant_id,
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "conversation_id": conversation_id,
            "status": status,
            "priority": priority,
            "assignee_id": None if index == 1 else f"agent-{tenant_id}-{index}",
            "sla_due_at": f"2026-09-{21 + index:02d}T00:00:00Z",
            "created_at": created_at,
            "updated_at": created_at,
        }
        for index, (ticket_id, customer_id, conversation_id, status, priority, created_at)
        in enumerate(ticket_specs, start=1)
    ]
    ticket_events_v1 = [
        {
            "tenant_id": tenant_id,
            "event_id": event_id,
            "ticket_id": ticket_id,
            "actor_id": f"agent-{tenant_id}-1",
            "from_status": from_status,
            "to_status": to_status,
            "created_at": created_at,
        }
        for event_id, ticket_id, from_status, to_status, created_at in event_specs
    ]
    actions_v1 = [
        {
            "tenant_id": tenant_id,
            "action_id": action_id,
            "order_id": order_id,
            "ticket_id": ticket_id,
            "conversation_id": f"conversation-{tenant_id}-{index}",
            "customer_id": f"customer-{tenant_id}-{index}",
            "action_kind": action_kind,
            "action_status": action_status,
            "quoted_amount": quoted_amount,
            "approval_required": approval_required,
            "created_at": f"2026-09-{20 + index:02d}T02:00:00Z",
            "expires_at": f"2026-09-{20 + index:02d}T03:00:00Z",
            "confirmed_at": None,
            "terminal_at": None,
        }
        for index, (
            action_id,
            order_id,
            ticket_id,
            action_kind,
            action_status,
            quoted_amount,
            approval_required,
        ) in enumerate(action_specs, start=1)
    ]
    return {
        "orders_v1": orders_v1,
        "tickets_v1": tickets_v1,
        "ticket_events_v1": ticket_events_v1,
        "actions_v1": actions_v1,
    }


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def build_delivery(root: Path, tenant_id: str) -> Path:
    delivery_dir = root / tenant_id
    delivery_dir.mkdir(parents=True, exist_ok=True)
    datasets = _rows(tenant_id)
    descriptors = []
    for name in DATASET_NAMES:
        content = b"".join(
            (
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            for row in datasets[name]
        )
        (delivery_dir / f"{name}.jsonl").write_bytes(content)
        descriptors.append(
            {
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "dataset_name": name,
                "file_name": f"{name}.jsonl",
                "row_count": len(datasets[name]),
            }
        )

    tickets_watermark = max(row["updated_at"] for row in datasets["tickets_v1"])
    events_watermark = max(row["created_at"] for row in datasets["ticket_events_v1"])
    snapshot = {
        "completed_at": GENERATED_AT,
        "consistency": "single_connection_read_transaction",
        "started_at": "2026-09-24T00:00:00Z",
        "watermarks": {
            "orders_v1": {
                "kind": "unavailable",
                "reason": "orders_v1_has_no_reliable_change_timestamp",
            },
            "tickets_v1": {
                "kind": "max_timestamp",
                "field": "updated_at",
                "max_value": tickets_watermark,
            },
            "ticket_events_v1": {
                "kind": "max_timestamp",
                "field": "created_at",
                "max_value": events_watermark,
            },
            "actions_v1": {
                "kind": "unavailable",
                "reason": "actions_v1_has_no_reliable_change_timestamp",
            },
        },
    }
    snapshot_material = {
        "contract_sha256": CONTRACT_SHA256,
        "contract_version": CONTRACT_VERSION,
        "currency": "USD",
        "datasets": [
            {
                "content_sha256": item["content_sha256"],
                "dataset_name": item["dataset_name"],
                "row_count": item["row_count"],
            }
            for item in descriptors
        ],
        "tenant_id": tenant_id,
    }
    snapshot["snapshot_id"] = hashlib.sha256(_canonical_json(snapshot_material)).hexdigest()
    manifest = {
        "contract_name": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "currency": "USD",
        "datasets": descriptors,
        "encoding": "utf-8",
        "export_format_version": "v2",
        "generated_at": GENERATED_AT,
        "media_type": "application/x-ndjson",
        "schema_sha256": CONTRACT_SHA256,
        "snapshot": snapshot,
        "tenant_id": tenant_id,
    }
    (delivery_dir / "manifest.json").write_bytes(_canonical_json(manifest))
    return delivery_dir


def main() -> None:
    output = Path(__file__).resolve().parent / "evaluation_v1"
    build_delivery(output, "tenant-a")
    build_delivery(output, "tenant-b")


if __name__ == "__main__":
    main()
