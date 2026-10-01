from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business_data.export_v1 import (
    DATASET_FILE_NAMES,
    FORBIDDEN_EXPORT_FIELD_NAMES,
    canonical_decimal,
    canonical_timestamp,
    write_business_data_export_v1,
)
from app.business_data.models import DeliveryContext, ExportValidationError
from app.domain import TicketPriority, TicketStatus
from app.store import (
    OrderRow,
    PendingActionRow,
    SupportStore,
    TicketEventRow,
    TicketRow,
)

TENANT = "TENANT-EXPORT"
OTHER_TENANT = "TENANT-OTHER"
FIXED_TIME = datetime(2026, 9, 23, 3, 0, 0, 123456, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> SupportStore:
    store = SupportStore(f"sqlite:///{tmp_path / 'export.db'}")
    store.create_schema()
    store.seed_demo_data(tenant_id=TENANT)
    ticket = store.create_ticket(
        customer_id="CUST-001",
        conversation_id="CONV-EXPORT",
        reason="customer free text must never be exported",
        tenant_id=TENANT,
        priority=TicketPriority.NORMAL,
        sla_due_at=FIXED_TIME + timedelta(hours=8),
    )
    store.assign_ticket(
        ticket.ticket_id,
        assignee_id="agent:export",
        actor_id="agent:export",
        tenant_id=TENANT,
    )
    store.transition_ticket(
        ticket.ticket_id,
        target=TicketStatus.PENDING_CUSTOMER,
        actor_id="agent:export",
        note="internal transition note must never be exported",
        tenant_id=TENANT,
    )
    _insert_actions(store, ticket.ticket_id)
    with Session(store.engine) as session:
        session.add(
            OrderRow(
                tenant_id=OTHER_TENANT,
                order_id="ORD-OTHER",
                customer_id="CUST-OTHER",
                status="delivered",
                amount=1.0,
                refundable_amount=1.0,
                refund_status="none",
                return_status="none",
            )
        )
        session.commit()
    return store


def _insert_actions(store: SupportStore, ticket_id: str) -> None:
    pending = _action(
        action_id="ACT-PENDING",
        order_id="ORD-1001",
        customer_id="CUST-001",
        kind="refund",
        status="pending_confirmation",
        quoted_amount=0.1 + 0.2,
        created_at=FIXED_TIME,
        expires_at=FIXED_TIME + timedelta(minutes=10),
    )
    submitted = _action(
        action_id="ACT-SUBMITTED",
        order_id="ORD-1001",
        customer_id="CUST-001",
        kind="return_request",
        status="submitted",
        quoted_amount=None,
        approval_required=True,
        ticket_id=ticket_id,
        confirmed_at=FIXED_TIME + timedelta(minutes=1),
        terminal_at=FIXED_TIME + timedelta(minutes=2),
        created_at=FIXED_TIME,
        expires_at=FIXED_TIME + timedelta(minutes=10),
        result={"must_not_export": "receipt"},
    )
    cancelled = _action(
        action_id="ACT-CANCELLED",
        order_id="ORD-1002",
        customer_id="CUST-001",
        kind="refund",
        status="cancelled",
        quoted_amount=5.0,
        created_at=FIXED_TIME,
        expires_at=FIXED_TIME + timedelta(minutes=10),
    )
    expired = _action(
        action_id="ACT-EXPIRED",
        order_id="ORD-2001",
        customer_id="CUST-002",
        kind="refund",
        status="expired",
        quoted_amount=10.0,
        created_at=FIXED_TIME,
        expires_at=FIXED_TIME + timedelta(minutes=10),
    )
    with Session(store.engine) as session:
        session.add_all([pending, submitted, cancelled, expired])
        session.commit()


def _action(
    *,
    action_id: str,
    order_id: str,
    customer_id: str,
    kind: str,
    status: str,
    quoted_amount: float | None,
    created_at: datetime,
    expires_at: datetime,
    approval_required: bool = False,
    ticket_id: str | None = None,
    confirmed_at: datetime | None = None,
    terminal_at: datetime | None = None,
    result: dict[str, object] | None = None,
) -> PendingActionRow:
    return PendingActionRow(
        tenant_id=TENANT,
        action_id=action_id,
        order_id=order_id,
        quoted_amount=quoted_amount,
        snapshot={"private": "action snapshot"},
        snapshot_hash=f"hash-{action_id}",
        snapshot_version=1,
        version=1,
        legacy=False,
        approval_required=approval_required,
        confirmed_by="customer:export" if confirmed_at else None,
        confirmed_at=confirmed_at,
        confirmed_snapshot_hash=f"hash-{action_id}" if confirmed_at else None,
        terminal_at=terminal_at,
        ticket_id=ticket_id,
        conversation_id="CONV-EXPORT",
        customer_id=customer_id,
        kind=kind,
        payload={"private": "action payload"},
        status=status,
        result=result,
        created_at=created_at,
        expires_at=expires_at,
    )


def _context() -> DeliveryContext:
    return DeliveryContext(tenant_id=TENANT, currency="USD")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _contract() -> dict[str, object]:
    path = (
        Path(__file__).resolve().parents[1] / "contracts" / "business_data" / "v1" / "contract.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _persistence_snapshot(store: SupportStore) -> dict[str, list[dict[str, object]]]:
    with store.engine.connect() as connection:
        return {
            "orders": [dict(row) for row in connection.execute(select(OrderRow)).mappings()],
            "tickets": [dict(row) for row in connection.execute(select(TicketRow)).mappings()],
            "events": [dict(row) for row in connection.execute(select(TicketEventRow)).mappings()],
            "actions": [
                dict(row) for row in connection.execute(select(PendingActionRow)).mappings()
            ],
        }


def test_happy_path_writes_all_datasets_and_verified_manifest(
    store: SupportStore, tmp_path: Path
) -> None:
    result = write_business_data_export_v1(
        store.engine,
        _context(),
        tmp_path / "delivery",
        generated_at=FIXED_TIME,
    )

    assert result.output_path == tmp_path / "delivery"
    manifest = json.loads((result.output_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["contract_name"] == "supportops_business_data"
    assert manifest["contract_version"] == "v1"
    assert manifest["export_format_version"] == "v2"
    assert manifest["generated_at"] == "2026-09-23T03:00:00.123456Z"
    assert manifest["tenant_id"] == TENANT
    assert manifest["currency"] == "USD"
    assert manifest["encoding"] == "utf-8"
    assert manifest["media_type"] == "application/x-ndjson"
    contract_path = (
        Path(__file__).resolve().parents[1] / "contracts" / "business_data" / "v1" / "contract.json"
    )
    assert manifest["schema_sha256"] == hashlib.sha256(contract_path.read_bytes()).hexdigest()
    assert manifest["snapshot"] == {
        "consistency": "sqlite_single_connection_read_transaction",
        "started_at": "2026-09-23T03:00:00.123456Z",
        "completed_at": "2026-09-23T03:00:00.123456Z",
        "snapshot_id": manifest["snapshot"]["snapshot_id"],
        "watermarks": {
            "orders_v1": {
                "kind": "unavailable",
                "reason": "orders_v1_has_no_reliable_change_timestamp",
            },
            "tickets_v1": {
                "kind": "max_timestamp",
                "field": "updated_at",
                "max_value": manifest["snapshot"]["watermarks"]["tickets_v1"]["max_value"],
            },
            "ticket_events_v1": {
                "kind": "max_timestamp",
                "field": "created_at",
                "max_value": manifest["snapshot"]["watermarks"]["ticket_events_v1"]["max_value"],
            },
            "actions_v1": {
                "kind": "unavailable",
                "reason": "actions_v1_has_no_reliable_change_timestamp",
            },
        },
    }
    assert len(manifest["snapshot"]["snapshot_id"]) == 64

    expected_counts = {
        "orders_v1": 3,
        "tickets_v1": 1,
        "ticket_events_v1": 2,
        "actions_v1": 4,
    }
    for dataset in manifest["datasets"]:
        content = (result.output_path / dataset["file_name"]).read_bytes()
        assert dataset["dataset_name"] in DATASET_FILE_NAMES
        assert dataset["file_name"] == DATASET_FILE_NAMES[dataset["dataset_name"]]
        assert dataset["row_count"] == expected_counts[dataset["dataset_name"]]
        assert dataset["content_sha256"] == hashlib.sha256(content).hexdigest()


def test_export_uses_only_contract_fields_and_excludes_sensitive_data(
    store: SupportStore, tmp_path: Path
) -> None:
    output_path = tmp_path / "delivery"
    write_business_data_export_v1(store.engine, _context(), output_path, generated_at=FIXED_TIME)
    expected_fields = {
        dataset["name"]: {field["name"] for field in dataset["fields"]}
        for dataset in _contract()["datasets"]
    }

    for dataset_name, file_name in DATASET_FILE_NAMES.items():
        records = _read_jsonl(output_path / file_name)
        for record in records:
            assert set(record) == expected_fields[dataset_name]
            assert not set(record).intersection(FORBIDDEN_EXPORT_FIELD_NAMES)
            assert record["tenant_id"] == TENANT

    assert all(
        record["order_id"] != "ORD-OTHER" for record in _read_jsonl(output_path / "orders_v1.jsonl")
    )


def test_export_is_byte_deterministic_with_fixed_generated_at(
    store: SupportStore, tmp_path: Path
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_business_data_export_v1(store.engine, _context(), first, generated_at=FIXED_TIME)
    write_business_data_export_v1(store.engine, _context(), second, generated_at=FIXED_TIME)

    for file_name in [*DATASET_FILE_NAMES.values(), "manifest.json"]:
        assert (first / file_name).read_bytes() == (second / file_name).read_bytes()


def test_actions_mapping_preserves_contract_statuses_and_optional_nulls(
    store: SupportStore, tmp_path: Path
) -> None:
    output_path = tmp_path / "delivery"
    write_business_data_export_v1(store.engine, _context(), output_path, generated_at=FIXED_TIME)
    records = {
        record["action_id"]: record for record in _read_jsonl(output_path / "actions_v1.jsonl")
    }

    assert records["ACT-PENDING"]["action_status"] == "pending_confirmation"
    assert records["ACT-SUBMITTED"]["action_status"] == "submitted"
    assert records["ACT-CANCELLED"]["action_status"] == "cancelled"
    assert records["ACT-EXPIRED"]["action_status"] == "expired"
    assert records["ACT-PENDING"]["ticket_id"] is None
    assert records["ACT-PENDING"]["confirmed_at"] is None
    assert records["ACT-PENDING"]["terminal_at"] is None
    assert records["ACT-SUBMITTED"]["quoted_amount"] is None
    assert records["ACT-PENDING"]["quoted_amount"] == "0.30000000000000004"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0"),
        (199.0, "199"),
        (12.5, "12.5"),
        (0.1 + 0.2, "0.30000000000000004"),
        (1e20, "100000000000000000000"),
    ],
)
def test_decimal_canonicalization(value: float, expected: str) -> None:
    assert canonical_decimal(value) == expected


def test_decimal_canonicalization_rejects_non_finite_values() -> None:
    with pytest.raises(ExportValidationError, match="non_finite_decimal_value"):
        canonical_decimal(float("nan"))
    with pytest.raises(ExportValidationError, match="non_finite_decimal_value"):
        canonical_decimal(float("inf"))


def test_timestamp_canonicalization_uses_utc_z_and_handles_sqlite_utc_convention() -> None:
    offset = datetime(2026, 9, 23, 11, 0, 0, tzinfo=UTC)
    naive = datetime(2026, 9, 23, 3, 0, 0)

    assert canonical_timestamp(offset) == "2026-09-23T11:00:00Z"
    assert canonical_timestamp(naive) == "2026-09-23T03:00:00Z"


def test_export_is_read_only_for_business_state(store: SupportStore, tmp_path: Path) -> None:
    before = _persistence_snapshot(store)

    write_business_data_export_v1(
        store.engine,
        _context(),
        tmp_path / "delivery",
        generated_at=FIXED_TIME,
    )

    assert _persistence_snapshot(store) == before


def test_invalid_context_and_existing_destination_fail_closed(
    store: SupportStore, tmp_path: Path
) -> None:
    with pytest.raises(ExportValidationError, match="invalid_export_tenant_id"):
        DeliveryContext(tenant_id="", currency="USD")
    with pytest.raises(ExportValidationError, match="invalid_export_currency"):
        DeliveryContext(tenant_id=TENANT, currency="usd")

    destination = tmp_path / "delivery"
    destination.mkdir()
    (destination / "sentinel.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError, match="business_data_export_destination_exists"):
        write_business_data_export_v1(
            store.engine,
            _context(),
            destination,
            generated_at=FIXED_TIME,
        )
    assert (destination / "sentinel.txt").read_text(encoding="utf-8") == "preserve"
