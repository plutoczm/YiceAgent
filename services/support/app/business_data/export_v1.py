from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.engine import Connection, Engine

from app.business_data.models import DeliveryContext, ExportResult, ExportValidationError
from app.business_data.protocol import (
    CONTRACT_PATH,
    DATASET_FILE_NAMES,
    EXPORT_FORMAT_VERSION,
    SUPPORTED_ENCODING,
    SUPPORTED_MEDIA_TYPE,
    content_snapshot_id,
    load_contract,
    sha256_hex,
)
from app.business_data.serialization import (
    DECIMAL_PATTERN,
    canonical_decimal,
    canonical_json_bytes,
    canonical_json_line_bytes,
    canonical_timestamp,
)
from app.store import OrderRow, PendingActionRow, TicketEventRow, TicketRow

FORBIDDEN_EXPORT_FIELD_NAMES = {
    "body",
    "context",
    "details",
    "note",
    "payload",
    "prompt",
    "reason",
    "response",
    "result",
    "snapshot",
    "trace_id",
}
ACTION_KIND_MAP = {
    "refund": "refund",
    "return_request": "return_request",
}
ACTION_STATUS_MAP = {
    "pending_confirmation": "pending_confirmation",
    "pending_approval": "pending_approval",
    "submitted": "submitted",
    "cancelled": "cancelled",
    "expired": "expired",
    "rejected": "rejected",
    "invalidated": "invalidated",
}


def write_business_data_export_v1(
    engine: Engine,
    context: DeliveryContext,
    output_path: Path,
    *,
    generated_at: datetime | None = None,
) -> ExportResult:
    """Write one deterministic, contract-validated delivery without mutating business data."""

    if output_path.exists():
        raise FileExistsError("business_data_export_destination_exists")

    contract = _load_contract()
    snapshot_started_at = generated_at or datetime.now(UTC)
    with _read_snapshot(engine) as (connection, consistency):
        datasets = _collect_datasets(connection, context)
    snapshot_completed_at = generated_at or datetime.now(UTC)
    _validate_datasets(contract, datasets)
    generated_at_value = canonical_timestamp(generated_at or snapshot_completed_at)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = Path(
        tempfile.mkdtemp(prefix=f".{output_path.name}.tmp-", dir=output_path.parent)
    )
    try:
        manifest_datasets: list[dict[str, object]] = []
        for dataset in contract["datasets"]:
            dataset_name = dataset["name"]
            file_name = DATASET_FILE_NAMES[dataset_name]
            content = _jsonl_bytes(datasets[dataset_name])
            (temporary_path / file_name).write_bytes(content)
            manifest_datasets.append(
                {
                    "dataset_name": dataset_name,
                    "file_name": file_name,
                    "row_count": len(datasets[dataset_name]),
                    "content_sha256": _sha256(content),
                }
            )

        snapshot = {
            "consistency": consistency,
            "started_at": canonical_timestamp(snapshot_started_at),
            "completed_at": canonical_timestamp(snapshot_completed_at),
            "snapshot_id": content_snapshot_id(
                contract_sha256=sha256_hex(CONTRACT_PATH.read_bytes()),
                contract_version=contract["version"],
                tenant_id=context.tenant_id,
                currency=context.currency,
                datasets=manifest_datasets,
            ),
            "watermarks": _watermarks(datasets),
        }
        manifest: dict[str, object] = {
            "contract_name": contract["contract_name"],
            "contract_version": contract["version"],
            "export_format_version": EXPORT_FORMAT_VERSION,
            "generated_at": generated_at_value,
            "tenant_id": context.tenant_id,
            "currency": context.currency,
            "schema_sha256": sha256_hex(CONTRACT_PATH.read_bytes()),
            "encoding": SUPPORTED_ENCODING,
            "media_type": SUPPORTED_MEDIA_TYPE,
            "snapshot": snapshot,
            "datasets": manifest_datasets,
        }
        _validate_manifest(temporary_path, contract, manifest)
        (temporary_path / "manifest.json").write_bytes(_json_bytes(manifest))

        # The temporary directory is a sibling of the destination, so this is a
        # same-filesystem rename after every dataset and the manifest are complete.
        temporary_path.rename(output_path)
    except Exception:
        shutil.rmtree(temporary_path, ignore_errors=True)
        raise

    return ExportResult(output_path=output_path, manifest=manifest)


def _load_contract() -> dict[str, Any]:
    try:
        return load_contract()
    except ValueError as exc:
        raise ExportValidationError("unreadable_business_data_contract") from exc


@contextmanager
def _read_snapshot(engine: Engine) -> Iterator[tuple[Connection, str]]:
    """Yield one explicit read transaction for all dataset SELECT statements."""

    if engine.dialect.name == "postgresql":
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                yield connection, "postgresql_repeatable_read_read_only"
        return

    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            # Explicit BEGIN creates the one SQLite read transaction before the first
            # dataset SELECT. It is not a claim of PostgreSQL MVCC semantics.
            connection.exec_driver_sql("BEGIN")
            try:
                yield connection, "sqlite_single_connection_read_transaction"
            finally:
                connection.rollback()
        return

    with engine.connect() as connection:
        with connection.begin():
            yield connection, "single_connection_read_transaction"


def _collect_datasets(
    connection: Connection, context: DeliveryContext
) -> dict[str, list[dict[str, object]]]:
    # Only Core SELECT statements are issued. No business tool, ORM session mutation,
    # schema creation, seeding, audit write, or transaction commit is involved.
    return {
        "orders_v1": _orders(connection, context),
        "tickets_v1": _tickets(connection, context),
        "ticket_events_v1": _ticket_events(connection, context),
        "actions_v1": _actions(connection, context),
    }


def _orders(connection: Connection, context: DeliveryContext) -> list[dict[str, object]]:
    statement = (
        select(
            OrderRow.tenant_id,
            OrderRow.order_id,
            OrderRow.customer_id,
            OrderRow.status,
            OrderRow.amount,
            OrderRow.refundable_amount,
            OrderRow.refund_status,
            OrderRow.return_status,
        )
        .where(OrderRow.tenant_id == context.tenant_id)
        .order_by(OrderRow.order_id)
    )
    return [
        {
            "tenant_id": row["tenant_id"],
            "order_id": row["order_id"],
            "customer_id": row["customer_id"],
            "status": row["status"],
            "amount": canonical_decimal(row["amount"]),
            "refundable_amount": canonical_decimal(row["refundable_amount"]),
            "refund_status": row["refund_status"],
            "return_status": row["return_status"],
        }
        for row in connection.execute(statement).mappings()
    ]


def _tickets(connection: Connection, context: DeliveryContext) -> list[dict[str, object]]:
    statement = (
        select(
            TicketRow.tenant_id,
            TicketRow.ticket_id,
            TicketRow.customer_id,
            TicketRow.conversation_id,
            TicketRow.status,
            TicketRow.priority,
            TicketRow.assignee_id,
            TicketRow.sla_due_at,
            TicketRow.created_at,
            TicketRow.updated_at,
        )
        .where(TicketRow.tenant_id == context.tenant_id)
        .order_by(TicketRow.ticket_id)
    )
    return [
        {
            "tenant_id": row["tenant_id"],
            "ticket_id": row["ticket_id"],
            "customer_id": row["customer_id"],
            "conversation_id": row["conversation_id"],
            "status": row["status"],
            "priority": row["priority"],
            "assignee_id": row["assignee_id"],
            "sla_due_at": canonical_timestamp(row["sla_due_at"]),
            "created_at": canonical_timestamp(row["created_at"]),
            "updated_at": canonical_timestamp(row["updated_at"]),
        }
        for row in connection.execute(statement).mappings()
    ]


def _ticket_events(connection: Connection, context: DeliveryContext) -> list[dict[str, object]]:
    statement = (
        select(
            TicketEventRow.tenant_id,
            TicketEventRow.event_id,
            TicketEventRow.ticket_id,
            TicketEventRow.actor_id,
            TicketEventRow.from_status,
            TicketEventRow.to_status,
            TicketEventRow.created_at,
        )
        .where(TicketEventRow.tenant_id == context.tenant_id)
        .order_by(TicketEventRow.created_at, TicketEventRow.event_id)
    )
    return [
        {
            "tenant_id": row["tenant_id"],
            "event_id": row["event_id"],
            "ticket_id": row["ticket_id"],
            "actor_id": row["actor_id"],
            "from_status": row["from_status"],
            "to_status": row["to_status"],
            "created_at": canonical_timestamp(row["created_at"]),
        }
        for row in connection.execute(statement).mappings()
    ]


def _actions(connection: Connection, context: DeliveryContext) -> list[dict[str, object]]:
    statement = (
        select(
            PendingActionRow.tenant_id,
            PendingActionRow.action_id,
            PendingActionRow.order_id,
            PendingActionRow.ticket_id,
            PendingActionRow.conversation_id,
            PendingActionRow.customer_id,
            PendingActionRow.kind,
            PendingActionRow.status,
            PendingActionRow.quoted_amount,
            PendingActionRow.approval_required,
            PendingActionRow.created_at,
            PendingActionRow.expires_at,
            PendingActionRow.confirmed_at,
            PendingActionRow.terminal_at,
        )
        .where(PendingActionRow.tenant_id == context.tenant_id)
        .order_by(PendingActionRow.action_id)
    )

    records: list[dict[str, object]] = []
    for row in connection.execute(statement).mappings():
        records.append(
            {
                "tenant_id": row["tenant_id"],
                "action_id": row["action_id"],
                "order_id": row["order_id"],
                "ticket_id": row["ticket_id"],
                "conversation_id": row["conversation_id"],
                "customer_id": row["customer_id"],
                "action_kind": _map_action_kind(row["kind"]),
                "action_status": _map_action_status(row["status"]),
                "quoted_amount": (
                    canonical_decimal(row["quoted_amount"])
                    if row["quoted_amount"] is not None
                    else None
                ),
                "approval_required": row["approval_required"],
                "created_at": canonical_timestamp(row["created_at"]),
                "expires_at": canonical_timestamp(row["expires_at"]),
                "confirmed_at": (
                    canonical_timestamp(row["confirmed_at"])
                    if row["confirmed_at"] is not None
                    else None
                ),
                "terminal_at": (
                    canonical_timestamp(row["terminal_at"])
                    if row["terminal_at"] is not None
                    else None
                ),
            }
        )
    return records


def _map_action_kind(value: object) -> str:
    try:
        return ACTION_KIND_MAP[str(value)]
    except KeyError as exc:
        raise ExportValidationError("unsupported_persistence_action_kind") from exc


def _map_action_status(value: object) -> str:
    try:
        return ACTION_STATUS_MAP[str(value)]
    except KeyError as exc:
        raise ExportValidationError("unsupported_persistence_action_status") from exc


def _watermarks(datasets: dict[str, list[dict[str, object]]]) -> dict[str, dict[str, object]]:
    return {
        "orders_v1": {
            "kind": "unavailable",
            "reason": "orders_v1_has_no_reliable_change_timestamp",
        },
        "tickets_v1": _timestamp_watermark(datasets["tickets_v1"], "updated_at"),
        "ticket_events_v1": _timestamp_watermark(datasets["ticket_events_v1"], "created_at"),
        "actions_v1": {
            "kind": "unavailable",
            "reason": "actions_v1_has_no_reliable_change_timestamp",
        },
    }


def _timestamp_watermark(records: list[dict[str, object]], field_name: str) -> dict[str, object]:
    values = [record[field_name] for record in records]
    if not all(isinstance(value, str) for value in values):
        raise ExportValidationError("invalid_watermark_source")
    return {
        "kind": "max_timestamp",
        "field": field_name,
        "max_value": max(values, default=None),
    }


def _validate_datasets(
    contract: dict[str, Any], datasets: dict[str, list[dict[str, object]]]
) -> None:
    for dataset in contract["datasets"]:
        name = dataset["name"]
        if name not in datasets:
            raise ExportValidationError("missing_contract_dataset")
        expected_fields = {field["name"]: field for field in dataset["fields"]}
        primary_key_fields = dataset["primary_key"]["fields"]
        seen_primary_keys: set[tuple[object, ...]] = set()

        for record in datasets[name]:
            actual_names = set(record)
            if actual_names != set(expected_fields):
                raise ExportValidationError(f"contract_field_mismatch:{name}")
            if actual_names & FORBIDDEN_EXPORT_FIELD_NAMES:
                raise ExportValidationError(f"forbidden_export_field:{name}")

            for field_name, specification in expected_fields.items():
                _validate_field_value(name, field_name, record[field_name], specification)

            primary_key = tuple(record[field_name] for field_name in primary_key_fields)
            if any(value is None or value == "" for value in primary_key):
                raise ExportValidationError(f"empty_primary_key:{name}")
            if primary_key in seen_primary_keys:
                raise ExportValidationError(f"duplicate_primary_key:{name}")
            seen_primary_keys.add(primary_key)


def _validate_field_value(
    dataset_name: str,
    field_name: str,
    value: object,
    specification: dict[str, Any],
) -> None:
    if value is None:
        if specification["nullable"]:
            return
        raise ExportValidationError(f"null_required_field:{dataset_name}.{field_name}")

    logical_type = specification["logical_type"]
    if logical_type == "identifier":
        if not isinstance(value, str) or not value:
            raise ExportValidationError(f"invalid_identifier:{dataset_name}.{field_name}")
    elif logical_type == "string":
        if not isinstance(value, str):
            raise ExportValidationError(f"invalid_string:{dataset_name}.{field_name}")
    elif logical_type == "enum":
        if value not in specification["enum_values"]:
            raise ExportValidationError(f"invalid_enum:{dataset_name}.{field_name}")
    elif logical_type == "decimal":
        if not isinstance(value, str) or not DECIMAL_PATTERN.fullmatch(value):
            raise ExportValidationError(f"invalid_decimal:{dataset_name}.{field_name}")
    elif logical_type == "timestamp":
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ExportValidationError(f"invalid_timestamp:{dataset_name}.{field_name}")
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as exc:
            raise ExportValidationError(f"invalid_timestamp:{dataset_name}.{field_name}") from exc
        if canonical_timestamp(parsed) != value:
            raise ExportValidationError(f"noncanonical_timestamp:{dataset_name}.{field_name}")
    elif logical_type == "boolean":
        if not isinstance(value, bool):
            raise ExportValidationError(f"invalid_boolean:{dataset_name}.{field_name}")
    else:
        raise ExportValidationError(f"unsupported_logical_type:{dataset_name}.{field_name}")


def _validate_manifest(
    directory: Path,
    contract: dict[str, Any],
    manifest: dict[str, object],
) -> None:
    if manifest["contract_name"] != contract["contract_name"]:
        raise ExportValidationError("manifest_contract_name_mismatch")
    if manifest["contract_version"] != contract["version"]:
        raise ExportValidationError("manifest_contract_version_mismatch")

    datasets = manifest["datasets"]
    if not isinstance(datasets, list):
        raise ExportValidationError("invalid_manifest_datasets")
    expected_names = [dataset["name"] for dataset in contract["datasets"]]
    if [dataset["dataset_name"] for dataset in datasets] != expected_names:
        raise ExportValidationError("manifest_dataset_order_mismatch")

    for dataset in datasets:
        file_name = dataset["file_name"]
        content = (directory / file_name).read_bytes()
        if dataset["row_count"] != content.count(b"\n"):
            raise ExportValidationError("manifest_row_count_mismatch")
        if dataset["content_sha256"] != _sha256(content):
            raise ExportValidationError("manifest_content_hash_mismatch")


def _jsonl_bytes(records: list[dict[str, object]]) -> bytes:
    return b"".join(canonical_json_line_bytes(record) for record in records)


def _json_bytes(value: object) -> bytes:
    return canonical_json_bytes(value)


def _sha256(content: bytes) -> str:
    return sha256_hex(content)
