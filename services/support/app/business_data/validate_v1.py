from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.business_data.models import (
    DeliveryContext,
    DeliveryValidationError,
    DeliveryValidationResult,
    ExportValidationError,
)
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
    canonical_json_bytes,
    canonical_json_line_bytes,
    canonical_timestamp,
)

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_CONSISTENCY = {
    "postgresql_repeatable_read_read_only",
    "sqlite_single_connection_read_transaction",
    "single_connection_read_transaction",
}
_FORBIDDEN_FIELD_NAMES = {
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
_MANIFEST_FIELDS = {
    "contract_name",
    "contract_version",
    "export_format_version",
    "generated_at",
    "tenant_id",
    "currency",
    "schema_sha256",
    "encoding",
    "media_type",
    "snapshot",
    "datasets",
}
_DESCRIPTOR_FIELDS = {"dataset_name", "file_name", "row_count", "content_sha256"}
_SNAPSHOT_FIELDS = {"consistency", "started_at", "completed_at", "snapshot_id", "watermarks"}


def validate_business_data_delivery_v1(delivery_path: Path) -> DeliveryValidationResult:
    """Validate an untrusted v2 delivery directory without accessing SupportOps OLTP."""

    supplied_path = Path(delivery_path)
    if not supplied_path.is_dir() or supplied_path.is_symlink():
        raise DeliveryValidationError("invalid_delivery_directory")
    root = supplied_path.resolve()

    manifest_path = _safe_delivery_file(root, "manifest.json", allow_manifest=True)
    manifest_bytes = _read_regular_file(manifest_path, MAX_MANIFEST_BYTES)
    if manifest_bytes.startswith(b"\xef\xbb\xbf"):
        raise DeliveryValidationError("manifest_bom_forbidden")
    manifest = _parse_json_object(manifest_bytes, "invalid_manifest_json")
    if manifest_bytes != canonical_json_bytes(manifest):
        raise DeliveryValidationError("noncanonical_manifest")

    contract = load_contract()
    descriptors = _validate_manifest(manifest, contract)
    expected_names = [dataset["name"] for dataset in contract["datasets"]]
    for descriptor in descriptors:
        _safe_delivery_file(root, descriptor["file_name"])
    allowed_files = {"manifest.json", *(descriptor["file_name"] for descriptor in descriptors)}
    entries = {entry.name for entry in root.iterdir()}
    if entries != allowed_files:
        raise DeliveryValidationError("unexpected_delivery_entry")

    primary_keys: dict[str, set[tuple[object, ...]]] = {}
    dataset_counts: dict[str, int] = {}
    observed_watermarks: dict[str, dict[str, object]] = {}
    datasets_by_name = {dataset["name"]: dataset for dataset in contract["datasets"]}

    for dataset_name in expected_names:
        descriptor = next(item for item in descriptors if item["dataset_name"] == dataset_name)
        dataset = datasets_by_name[dataset_name]
        file_path = _safe_delivery_file(root, descriptor["file_name"])
        count, keys, watermark = _validate_dataset_file(
            file_path,
            descriptor,
            dataset,
            primary_keys,
            manifest["tenant_id"],
        )
        primary_keys[dataset_name] = keys
        dataset_counts[dataset_name] = count
        observed_watermarks[dataset_name] = watermark

    snapshot = manifest["snapshot"]
    _validate_snapshot(snapshot, observed_watermarks, manifest, descriptors)
    delivery_fingerprint = _delivery_fingerprint(manifest_bytes, descriptors)

    return DeliveryValidationResult(
        valid=True,
        contract_version=manifest["contract_version"],
        export_format_version=manifest["export_format_version"],
        tenant_id=manifest["tenant_id"],
        currency=manifest["currency"],
        dataset_row_counts=dataset_counts,
        snapshot=snapshot,
        delivery_fingerprint=delivery_fingerprint,
    )


def _validate_manifest(
    manifest: dict[str, object], contract: dict[str, Any]
) -> list[dict[str, object]]:
    if set(manifest) != _MANIFEST_FIELDS:
        raise DeliveryValidationError("manifest_fields_mismatch")
    if manifest["contract_name"] != contract["contract_name"]:
        raise DeliveryValidationError("manifest_contract_name_mismatch")
    if manifest["contract_version"] != contract["version"]:
        raise DeliveryValidationError("manifest_contract_version_mismatch")
    if manifest["export_format_version"] != EXPORT_FORMAT_VERSION:
        raise DeliveryValidationError("unsupported_export_format_version")
    _validate_context(manifest["tenant_id"], manifest["currency"])
    _parse_utc_timestamp(manifest["generated_at"], "invalid_generated_at")
    if manifest["schema_sha256"] != sha256_hex(CONTRACT_PATH.read_bytes()):
        raise DeliveryValidationError("contract_hash_mismatch")
    if manifest["encoding"] != SUPPORTED_ENCODING or manifest["media_type"] != SUPPORTED_MEDIA_TYPE:
        raise DeliveryValidationError("unsupported_delivery_encoding")

    descriptors = manifest["datasets"]
    if not isinstance(descriptors, list):
        raise DeliveryValidationError("invalid_manifest_datasets")
    expected_names = list(DATASET_FILE_NAMES)
    if len(descriptors) != len(expected_names):
        raise DeliveryValidationError("manifest_dataset_count_mismatch")

    names: list[str] = []
    filenames: set[str] = set()
    validated: list[dict[str, object]] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict) or set(descriptor) != _DESCRIPTOR_FIELDS:
            raise DeliveryValidationError("invalid_manifest_dataset")
        dataset_name = descriptor["dataset_name"]
        file_name = descriptor["file_name"]
        row_count = descriptor["row_count"]
        content_sha256 = descriptor["content_sha256"]
        if not isinstance(dataset_name, str) or dataset_name not in DATASET_FILE_NAMES:
            raise DeliveryValidationError("unknown_manifest_dataset")
        if file_name != DATASET_FILE_NAMES[dataset_name] or not _safe_basename(file_name):
            raise DeliveryValidationError("unsafe_dataset_filename")
        if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
            raise DeliveryValidationError("invalid_manifest_row_count")
        if not isinstance(content_sha256, str) or not _SHA256_PATTERN.fullmatch(content_sha256):
            raise DeliveryValidationError("invalid_manifest_content_hash")
        if dataset_name in names or file_name in filenames:
            raise DeliveryValidationError("duplicate_manifest_dataset")
        names.append(dataset_name)
        filenames.add(file_name)
        validated.append(descriptor)

    if names != expected_names:
        raise DeliveryValidationError("manifest_dataset_order_mismatch")
    return validated


def _validate_context(tenant_id: object, currency: object) -> None:
    try:
        DeliveryContext(tenant_id=tenant_id, currency=currency)
    except ExportValidationError as exc:
        raise DeliveryValidationError("invalid_delivery_context") from exc


def _validate_snapshot(
    snapshot: object,
    observed_watermarks: dict[str, dict[str, object]],
    manifest: dict[str, object],
    descriptors: list[dict[str, object]],
) -> None:
    if not isinstance(snapshot, dict) or set(snapshot) != _SNAPSHOT_FIELDS:
        raise DeliveryValidationError("invalid_snapshot_metadata")
    if snapshot["consistency"] not in _ALLOWED_CONSISTENCY:
        raise DeliveryValidationError("unsupported_snapshot_consistency")
    started_at = _parse_utc_timestamp(snapshot["started_at"], "invalid_snapshot_started_at")
    completed_at = _parse_utc_timestamp(snapshot["completed_at"], "invalid_snapshot_completed_at")
    if completed_at < started_at:
        raise DeliveryValidationError("invalid_snapshot_time_order")
    snapshot_id = snapshot["snapshot_id"]
    if not isinstance(snapshot_id, str) or not _SHA256_PATTERN.fullmatch(snapshot_id):
        raise DeliveryValidationError("invalid_snapshot_id")
    expected_snapshot_id = content_snapshot_id(
        contract_sha256=manifest["schema_sha256"],
        contract_version=manifest["contract_version"],
        tenant_id=manifest["tenant_id"],
        currency=manifest["currency"],
        datasets=descriptors,
    )
    if snapshot_id != expected_snapshot_id:
        raise DeliveryValidationError("snapshot_id_mismatch")
    if snapshot["watermarks"] != observed_watermarks:
        raise DeliveryValidationError("watermark_mismatch")


def _validate_dataset_file(
    path: Path,
    descriptor: dict[str, object],
    dataset: dict[str, Any],
    known_primary_keys: dict[str, set[tuple[object, ...]]],
    expected_tenant_id: str,
) -> tuple[int, set[tuple[object, ...]], dict[str, object]]:
    hasher = sha256()
    count = 0
    primary_keys: set[tuple[object, ...]] = set()
    timestamp_values: dict[str, str | None] = {}

    with path.open("rb") as stream:
        while line := stream.readline(MAX_LINE_BYTES + 1):
            if len(line) > MAX_LINE_BYTES:
                raise DeliveryValidationError("dataset_line_too_large")
            if not line.endswith(b"\n") or line == b"\n":
                raise DeliveryValidationError("invalid_dataset_line_boundary")
            if count == 0 and line.startswith(b"\xef\xbb\xbf"):
                raise DeliveryValidationError("dataset_bom_forbidden")
            try:
                text = line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise DeliveryValidationError("invalid_dataset_utf8") from exc
            record = _parse_json_object(text.encode("utf-8"), "invalid_dataset_json")
            if canonical_json_line_bytes(record) != line:
                raise DeliveryValidationError("noncanonical_dataset_json")
            primary_key = _validate_record(
                record,
                dataset,
                known_primary_keys,
                expected_tenant_id,
            )
            if primary_key in primary_keys:
                raise DeliveryValidationError("duplicate_dataset_primary_key")
            primary_keys.add(primary_key)
            for field_name in ("updated_at", "created_at"):
                value = record.get(field_name)
                if isinstance(value, str):
                    timestamp_values[field_name] = max(
                        timestamp_values.get(field_name) or value, value
                    )
            hasher.update(line)
            count += 1

    if count != descriptor["row_count"]:
        raise DeliveryValidationError("manifest_row_count_mismatch")
    if hasher.hexdigest() != descriptor["content_sha256"]:
        raise DeliveryValidationError("manifest_content_hash_mismatch")
    return count, primary_keys, _watermark_for(dataset["name"], timestamp_values)


def _validate_record(
    record: dict[str, object],
    dataset: dict[str, Any],
    known_primary_keys: dict[str, set[tuple[object, ...]]],
    expected_tenant_id: str,
) -> tuple[object, ...]:
    expected_fields = {field["name"]: field for field in dataset["fields"]}
    if set(record) != set(expected_fields):
        raise DeliveryValidationError("dataset_fields_mismatch")
    if set(record) & _FORBIDDEN_FIELD_NAMES:
        raise DeliveryValidationError("forbidden_delivery_field")
    if record.get("tenant_id") != expected_tenant_id:
        raise DeliveryValidationError("delivery_tenant_mismatch")

    for field_name, specification in expected_fields.items():
        _validate_field_value(record[field_name], specification)
    primary_key = tuple(record[field_name] for field_name in dataset["primary_key"]["fields"])
    if any(value is None or value == "" for value in primary_key):
        raise DeliveryValidationError("empty_dataset_primary_key")

    for relationship in dataset["relationships"]:
        value = record[relationship["field"]]
        if value is None and relationship["nullable"]:
            continue
        target = known_primary_keys.get(relationship["target_dataset"])
        target_key = tuple(
            record["tenant_id"] if name == "tenant_id" else record[relationship["field"]]
            for name in relationship["target_primary_key"]
        )
        if target is None or target_key not in target:
            raise DeliveryValidationError("broken_dataset_relationship")
    return primary_key


def _validate_field_value(value: object, specification: dict[str, Any]) -> None:
    if value is None:
        if specification["nullable"]:
            return
        raise DeliveryValidationError("null_required_field")

    logical_type = specification["logical_type"]
    if logical_type == "identifier":
        if not isinstance(value, str) or not value:
            raise DeliveryValidationError("invalid_identifier")
    elif logical_type == "string":
        if not isinstance(value, str):
            raise DeliveryValidationError("invalid_string")
    elif logical_type == "enum":
        if value not in specification["enum_values"]:
            raise DeliveryValidationError("invalid_enum")
    elif logical_type == "decimal":
        if not isinstance(value, str) or not DECIMAL_PATTERN.fullmatch(value):
            raise DeliveryValidationError("invalid_decimal")
    elif logical_type == "timestamp":
        _parse_utc_timestamp(value, "invalid_timestamp")
    elif logical_type == "boolean":
        if not isinstance(value, bool):
            raise DeliveryValidationError("invalid_boolean")
    else:
        raise DeliveryValidationError("unsupported_logical_type")


def _watermark_for(dataset_name: str, timestamp_values: dict[str, str | None]) -> dict[str, object]:
    if dataset_name == "tickets_v1":
        return {
            "kind": "max_timestamp",
            "field": "updated_at",
            "max_value": timestamp_values.get("updated_at"),
        }
    if dataset_name == "ticket_events_v1":
        return {
            "kind": "max_timestamp",
            "field": "created_at",
            "max_value": timestamp_values.get("created_at"),
        }
    if dataset_name == "orders_v1":
        return {
            "kind": "unavailable",
            "reason": "orders_v1_has_no_reliable_change_timestamp",
        }
    return {
        "kind": "unavailable",
        "reason": "actions_v1_has_no_reliable_change_timestamp",
    }


def _delivery_fingerprint(manifest_bytes: bytes, descriptors: list[dict[str, object]]) -> str:
    hashes = b"\n".join(item["content_sha256"].encode("ascii") for item in descriptors)
    return sha256_hex(manifest_bytes + b"\n" + hashes)


def _safe_delivery_file(root: Path, file_name: str, *, allow_manifest: bool = False) -> Path:
    if not _safe_basename(file_name, allow_manifest=allow_manifest):
        raise DeliveryValidationError("unsafe_delivery_path")
    candidate = root / file_name
    if candidate.is_symlink() or not candidate.is_file():
        raise DeliveryValidationError("invalid_delivery_file")
    if candidate.resolve().parent != root:
        raise DeliveryValidationError("delivery_path_escape")
    return candidate


def _safe_basename(value: object, *, allow_manifest: bool = False) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == Path(value).name
        and "/" not in value
        and "\\" not in value
        and ":" not in value
        and (allow_manifest or value != "manifest.json")
    )


def _read_regular_file(path: Path, maximum_size: int) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum_size:
        raise DeliveryValidationError("invalid_delivery_file")
    return path.read_bytes()


def _parse_json_object(content: bytes, error_code: str) -> dict[str, object]:
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, DeliveryValidationError) as exc:
        raise DeliveryValidationError(error_code) from exc
    if not isinstance(value, dict):
        raise DeliveryValidationError(error_code)
    return value


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise DeliveryValidationError("duplicate_json_key")
        value[key] = item
    return value


def _parse_utc_timestamp(value: object, error_code: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DeliveryValidationError(error_code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise DeliveryValidationError(error_code) from exc
    if parsed.tzinfo is None or parsed.astimezone(UTC) != parsed:
        raise DeliveryValidationError(error_code)
    if canonical_timestamp(parsed) != value:
        raise DeliveryValidationError(error_code)
    return parsed
