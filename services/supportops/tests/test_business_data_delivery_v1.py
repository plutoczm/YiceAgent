from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.business_data.compatibility_v1 import (
    assert_contract_v1_compatible,
    check_current_contract_v1_compatibility,
    compatibility_surface,
)
from app.business_data.export_v1 import _read_snapshot, write_business_data_export_v1
from app.business_data.models import (
    ContractCompatibilityError,
    DeliveryContext,
    DeliveryValidationError,
)
from app.business_data.protocol import DATASET_FILE_NAMES, load_contract, sha256_hex
from app.business_data.serialization import canonical_json_bytes, canonical_json_line_bytes
from app.business_data.validate_v1 import validate_business_data_delivery_v1
from app.domain import TicketPriority
from app.store import OrderRow, PendingActionRow, SupportStore

TENANT = "TENANT-DELIVERY"
FIXED_TIME = datetime(2026, 9, 23, 3, 0, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> SupportStore:
    store = SupportStore(f"sqlite:///{tmp_path / 'delivery.db'}")
    store.create_schema()
    store.seed_demo_data(tenant_id=TENANT)
    ticket = store.create_ticket(
        customer_id="CUST-001",
        conversation_id="CONV-DELIVERY",
        reason="never export this text",
        tenant_id=TENANT,
        priority=TicketPriority.NORMAL,
        sla_due_at=FIXED_TIME + timedelta(hours=8),
    )
    store.assign_ticket(
        ticket.ticket_id,
        assignee_id="agent:delivery",
        actor_id="agent:delivery",
        tenant_id=TENANT,
    )
    with Session(store.engine) as session:
        session.add(
            PendingActionRow(
                tenant_id=TENANT,
                action_id="ACT-DELIVERY",
                order_id="ORD-1001",
                quoted_amount=12.5,
                snapshot={"private": "snapshot"},
                snapshot_hash="delivery-snapshot",
                snapshot_version=1,
                version=1,
                legacy=False,
                approval_required=False,
                confirmed_by=None,
                confirmed_at=None,
                confirmed_snapshot_hash=None,
                terminal_at=None,
                ticket_id=None,
                conversation_id="CONV-DELIVERY",
                customer_id="CUST-001",
                kind="refund",
                payload={"private": "payload"},
                status="pending_confirmation",
                result=None,
                created_at=FIXED_TIME,
                expires_at=FIXED_TIME + timedelta(minutes=10),
            )
        )
        session.commit()
    yield store
    store.engine.dispose()


def _context() -> DeliveryContext:
    return DeliveryContext(tenant_id=TENANT, currency="USD")


def _delivery(store: SupportStore, tmp_path: Path, name: str = "delivery") -> Path:
    output = tmp_path / name
    write_business_data_export_v1(store.engine, _context(), output, generated_at=FIXED_TIME)
    return output


def _manifest(delivery: Path) -> dict[str, object]:
    return json.loads((delivery / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(delivery: Path, manifest: dict[str, object]) -> None:
    (delivery / "manifest.json").write_bytes(canonical_json_bytes(manifest))


def _descriptor(manifest: dict[str, object], dataset_name: str) -> dict[str, object]:
    datasets = manifest["datasets"]
    assert isinstance(datasets, list)
    return next(dataset for dataset in datasets if dataset["dataset_name"] == dataset_name)


def _refresh_descriptor(delivery: Path, manifest: dict[str, object], dataset_name: str) -> None:
    descriptor = _descriptor(manifest, dataset_name)
    content = (delivery / descriptor["file_name"]).read_bytes()
    descriptor["row_count"] = content.count(b"\n")
    descriptor["content_sha256"] = sha256_hex(content)


def _first_record(delivery: Path, dataset_name: str) -> dict[str, object]:
    path = delivery / DATASET_FILE_NAMES[dataset_name]
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


def _replace_first_record(delivery: Path, dataset_name: str, record: dict[str, object]) -> None:
    path = delivery / DATASET_FILE_NAMES[dataset_name]
    lines = path.read_bytes().splitlines(keepends=True)
    lines[0] = canonical_json_line_bytes(record)
    path.write_bytes(b"".join(lines))


def test_validator_accepts_exported_delivery_and_returns_stable_fingerprint(
    store: SupportStore, tmp_path: Path
) -> None:
    first = _delivery(store, tmp_path, "first")
    second = _delivery(store, tmp_path, "second")

    first_result = validate_business_data_delivery_v1(first)
    second_result = validate_business_data_delivery_v1(second)

    assert first_result.valid is True
    assert first_result.contract_version == "v1"
    assert first_result.export_format_version == "v2"
    assert first_result.tenant_id == TENANT
    assert first_result.currency == "USD"
    assert first_result.dataset_row_counts == {
        "orders_v1": 3,
        "tickets_v1": 1,
        "ticket_events_v1": 1,
        "actions_v1": 1,
    }
    assert first_result.delivery_fingerprint == second_result.delivery_fingerprint
    assert first_result.snapshot["watermarks"]["orders_v1"]["kind"] == "unavailable"
    assert first_result.snapshot["watermarks"]["actions_v1"]["kind"] == "unavailable"
    assert first_result.snapshot["watermarks"]["tickets_v1"]["field"] == "updated_at"
    assert first_result.snapshot["watermarks"]["ticket_events_v1"]["field"] == "created_at"


def test_validator_rejects_tampered_dataset_hash(store: SupportStore, tmp_path: Path) -> None:
    delivery = _delivery(store, tmp_path)
    path = delivery / "orders_v1.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"amount":"199"', b'"amount":"299"', 1))
    with pytest.raises(DeliveryValidationError, match="manifest_content_hash_mismatch"):
        validate_business_data_delivery_v1(delivery)


def test_validator_rejects_manifest_count_and_contract_hash(
    store: SupportStore, tmp_path: Path
) -> None:
    delivery = _delivery(store, tmp_path)
    manifest = _manifest(delivery)
    _descriptor(manifest, "orders_v1")["row_count"] = 999
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="manifest_row_count_mismatch"):
        validate_business_data_delivery_v1(delivery)

    delivery = _delivery(store, tmp_path, "contract-hash")
    manifest = _manifest(delivery)
    manifest["schema_sha256"] = "0" * 64
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="contract_hash_mismatch"):
        validate_business_data_delivery_v1(delivery)


def test_validator_rejects_missing_unknown_and_duplicate_datasets(
    store: SupportStore, tmp_path: Path
) -> None:
    delivery = _delivery(store, tmp_path)
    (delivery / "orders_v1.jsonl").unlink()
    with pytest.raises(DeliveryValidationError, match="invalid_delivery_file"):
        validate_business_data_delivery_v1(delivery)

    delivery = _delivery(store, tmp_path, "unknown")
    manifest = _manifest(delivery)
    unknown = copy.deepcopy(_descriptor(manifest, "orders_v1"))
    unknown["dataset_name"] = "unknown_v1"
    manifest["datasets"].append(unknown)
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="manifest_dataset_count_mismatch"):
        validate_business_data_delivery_v1(delivery)

    delivery = _delivery(store, tmp_path, "duplicate")
    manifest = _manifest(delivery)
    manifest["datasets"][1] = copy.deepcopy(manifest["datasets"][0])
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="duplicate_manifest_dataset"):
        validate_business_data_delivery_v1(delivery)

    delivery = _delivery(store, tmp_path, "extra-entry")
    (delivery / "unexpected.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(DeliveryValidationError, match="unexpected_delivery_entry"):
        validate_business_data_delivery_v1(delivery)


@pytest.mark.parametrize("file_name", ["../orders_v1.jsonl", "C:\\orders_v1.jsonl"])
def test_validator_rejects_unsafe_dataset_filename(
    store: SupportStore, tmp_path: Path, file_name: str
) -> None:
    delivery = _delivery(store, tmp_path)
    manifest = _manifest(delivery)
    _descriptor(manifest, "orders_v1")["file_name"] = file_name
    _write_manifest(delivery, manifest)

    with pytest.raises(DeliveryValidationError, match="unsafe_dataset_filename"):
        validate_business_data_delivery_v1(delivery)


def test_validator_rejects_malformed_and_noncanonical_jsonl(
    store: SupportStore, tmp_path: Path
) -> None:
    delivery = _delivery(store, tmp_path)
    path = delivery / "orders_v1.jsonl"
    path.write_bytes(b"{not-json}\n")
    manifest = _manifest(delivery)
    _refresh_descriptor(delivery, manifest, "orders_v1")
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="invalid_dataset_json"):
        validate_business_data_delivery_v1(delivery)

    delivery = _delivery(store, tmp_path, "noncanonical")
    record = _first_record(delivery, "orders_v1")
    path = delivery / "orders_v1.jsonl"
    path.write_bytes((json.dumps(record, sort_keys=False) + "\n").encode("utf-8"))
    manifest = _manifest(delivery)
    _refresh_descriptor(delivery, manifest, "orders_v1")
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="noncanonical_dataset_json"):
        validate_business_data_delivery_v1(delivery)


def test_validator_rejects_schema_decimal_datetime_and_primary_key_breakage(
    store: SupportStore, tmp_path: Path
) -> None:
    delivery = _delivery(store, tmp_path)
    record = _first_record(delivery, "orders_v1")
    record["reason"] = "forbidden"
    _replace_first_record(delivery, "orders_v1", record)
    manifest = _manifest(delivery)
    _refresh_descriptor(delivery, manifest, "orders_v1")
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="dataset_fields_mismatch"):
        validate_business_data_delivery_v1(delivery)

    delivery = _delivery(store, tmp_path, "missing-field")
    record = _first_record(delivery, "orders_v1")
    del record["customer_id"]
    _replace_first_record(delivery, "orders_v1", record)
    manifest = _manifest(delivery)
    _refresh_descriptor(delivery, manifest, "orders_v1")
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="dataset_fields_mismatch"):
        validate_business_data_delivery_v1(delivery)

    delivery = _delivery(store, tmp_path, "decimal")
    record = _first_record(delivery, "orders_v1")
    record["amount"] = "01.0"
    _replace_first_record(delivery, "orders_v1", record)
    manifest = _manifest(delivery)
    _refresh_descriptor(delivery, manifest, "orders_v1")
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="invalid_decimal"):
        validate_business_data_delivery_v1(delivery)

    delivery = _delivery(store, tmp_path, "datetime")
    record = _first_record(delivery, "tickets_v1")
    record["created_at"] = "2026-09-23T03:00:00+00:00"
    _replace_first_record(delivery, "tickets_v1", record)
    manifest = _manifest(delivery)
    _refresh_descriptor(delivery, manifest, "tickets_v1")
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="invalid_timestamp"):
        validate_business_data_delivery_v1(delivery)

    delivery = _delivery(store, tmp_path, "duplicate-pk")
    path = delivery / "orders_v1.jsonl"
    path.write_bytes(path.read_bytes() + path.read_bytes().splitlines(keepends=True)[0])
    manifest = _manifest(delivery)
    _refresh_descriptor(delivery, manifest, "orders_v1")
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="duplicate_dataset_primary_key"):
        validate_business_data_delivery_v1(delivery)


def test_validator_rejects_enum_nullability_and_relationship_breakage(
    store: SupportStore, tmp_path: Path
) -> None:
    delivery = _delivery(store, tmp_path)
    record = _first_record(delivery, "tickets_v1")
    record["status"] = "not_a_status"
    _replace_first_record(delivery, "tickets_v1", record)
    manifest = _manifest(delivery)
    _refresh_descriptor(delivery, manifest, "tickets_v1")
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="invalid_enum"):
        validate_business_data_delivery_v1(delivery)

    delivery = _delivery(store, tmp_path, "nullability")
    record = _first_record(delivery, "orders_v1")
    record["order_id"] = None
    _replace_first_record(delivery, "orders_v1", record)
    manifest = _manifest(delivery)
    _refresh_descriptor(delivery, manifest, "orders_v1")
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="null_required_field"):
        validate_business_data_delivery_v1(delivery)

    delivery = _delivery(store, tmp_path, "relationship")
    record = _first_record(delivery, "ticket_events_v1")
    record["ticket_id"] = "TKT-NOT-IN-DELIVERY"
    _replace_first_record(delivery, "ticket_events_v1", record)
    manifest = _manifest(delivery)
    _refresh_descriptor(delivery, manifest, "ticket_events_v1")
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="broken_dataset_relationship"):
        validate_business_data_delivery_v1(delivery)


def test_validator_rejects_invalid_snapshot_and_watermark(
    store: SupportStore, tmp_path: Path
) -> None:
    delivery = _delivery(store, tmp_path)
    manifest = _manifest(delivery)
    manifest["snapshot"]["consistency"] = "invented_database_lsn"
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="unsupported_snapshot_consistency"):
        validate_business_data_delivery_v1(delivery)

    delivery = _delivery(store, tmp_path, "watermark")
    manifest = _manifest(delivery)
    manifest["snapshot"]["watermarks"]["orders_v1"] = {
        "kind": "max_timestamp",
        "field": "generated_at",
        "max_value": manifest["generated_at"],
    }
    _write_manifest(delivery, manifest)
    with pytest.raises(DeliveryValidationError, match="watermark_mismatch"):
        validate_business_data_delivery_v1(delivery)


def test_compatibility_gate_rejects_structural_breaks_and_allows_optional_addition() -> None:
    current = load_contract()
    baseline_path = (
        Path(__file__).resolve().parents[1]
        / "contracts"
        / "business_data"
        / "v1"
        / "compatibility_baseline.json"
    )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    check_current_contract_v1_compatibility()

    deleted = copy.deepcopy(current)
    deleted["datasets"][0]["fields"].pop()
    with pytest.raises(ContractCompatibilityError, match="field_removed"):
        assert_contract_v1_compatible(deleted, baseline)

    requiredness = copy.deepcopy(current)
    requiredness["datasets"][1]["fields"][6]["nullable"] = False
    with pytest.raises(ContractCompatibilityError, match="field_nullable_changed"):
        assert_contract_v1_compatible(requiredness, baseline)

    logical_type = copy.deepcopy(current)
    logical_type["datasets"][0]["fields"][3]["logical_type"] = "enum"
    with pytest.raises(ContractCompatibilityError, match="field_logical_type_changed"):
        assert_contract_v1_compatible(logical_type, baseline)

    primary_key = copy.deepcopy(current)
    primary_key["datasets"][0]["primary_key"]["fields"] = ["order_id"]
    with pytest.raises(ContractCompatibilityError, match="dataset_primary_key_changed"):
        assert_contract_v1_compatible(primary_key, baseline)

    enum_change = copy.deepcopy(current)
    enum_change["datasets"][1]["fields"][4]["enum_values"].append("new_state")
    with pytest.raises(ContractCompatibilityError, match="field_enum_changed"):
        assert_contract_v1_compatible(enum_change, baseline)

    additive = copy.deepcopy(current)
    additive["datasets"][0]["fields"].append(
        {
            "name": "optional_future_field",
            "description": "Future non-breaking field.",
            "logical_type": "string",
            "nullable": True,
            "semantic_meaning": "Optional future metadata.",
            "sensitivity": "internal",
        }
    )
    assert_contract_v1_compatible(additive, baseline)
    assert compatibility_surface(current) == baseline


def test_sqlite_snapshot_uses_one_read_transaction(store: SupportStore) -> None:
    with store.engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")

    with _read_snapshot(store.engine) as (reader, consistency):
        before = reader.scalar(select(func.count()).select_from(OrderRow))
        with store.engine.begin() as writer:
            writer.execute(
                OrderRow.__table__.insert().values(
                    tenant_id=TENANT,
                    order_id="ORD-SNAPSHOT",
                    customer_id="CUST-001",
                    status="delivered",
                    amount=1.0,
                    refundable_amount=1.0,
                    refund_status="none",
                    return_status="none",
                )
            )
        after = reader.scalar(select(func.count()).select_from(OrderRow))

    assert consistency == "sqlite_single_connection_read_transaction"
    assert before == after == 3


def test_validator_rejects_symlinked_dataset_when_supported(
    store: SupportStore, tmp_path: Path
) -> None:
    delivery = _delivery(store, tmp_path)
    target = tmp_path / "outside.jsonl"
    target.write_bytes((delivery / "orders_v1.jsonl").read_bytes())
    dataset = delivery / "orders_v1.jsonl"
    dataset.unlink()
    try:
        os.symlink(target, dataset)
    except OSError:
        pytest.skip("symlink_creation_not_available")
    with pytest.raises(DeliveryValidationError, match="invalid_delivery_file"):
        validate_business_data_delivery_v1(delivery)
