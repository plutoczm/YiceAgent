from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from backend.app.application.business_analytics.errors import (
    BusinessAnalyticsError,
    BusinessDataDeliveryError,
    BusinessDataIntegrityError,
    BusinessDataTenantMismatchError,
)
from backend.app.application.business_analytics.governed_models import (
    BusinessDataSnapshot,
    BusinessDatasetSnapshotMetadata,
    GovernedExecutionPolicy,
)
from backend.app.application.business_analytics.managed_models import (
    EffectiveBusinessCatalog,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessLogicalType,
)
from backend.app.infrastructure.business_data.contract_catalog import (
    AcceptedBusinessDataContract,
    BusinessDataContractCatalog,
)
from backend.app.infrastructure.business_data.models import (
    ExternalBusinessDataset,
    ExternalBusinessField,
)


EXPORT_FORMAT_VERSION = "v2"
SUPPORTED_ENCODING = "utf-8"
SUPPORTED_MEDIA_TYPE = "application/x-ndjson"
DATASET_FILE_NAMES = {
    "orders_v1": "orders_v1.jsonl",
    "tickets_v1": "tickets_v1.jsonl",
    "ticket_events_v1": "ticket_events_v1.jsonl",
    "actions_v1": "actions_v1.jsonl",
}
MANIFEST_FIELDS = {
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
DESCRIPTOR_FIELDS = {"dataset_name", "file_name", "row_count", "content_sha256"}
SNAPSHOT_FIELDS = {
    "consistency",
    "started_at",
    "completed_at",
    "snapshot_id",
    "watermarks",
}
ALLOWED_CONSISTENCY = {
    "postgresql_repeatable_read_read_only",
    "sqlite_single_connection_read_transaction",
    "single_connection_read_transaction",
}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
DECIMAL_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
FORBIDDEN_FIELD_NAMES = {
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


@dataclass(frozen=True)
class DatasetDescriptor:
    dataset_name: str
    file_name: str
    row_count: int
    content_sha256: str


@dataclass(frozen=True)
class DecimalShape:
    precision: int
    scale: int


@dataclass(frozen=True)
class DatasetScan:
    descriptor: DatasetDescriptor
    primary_keys: frozenset[tuple[str, ...]]
    decimal_shapes: Mapping[str, DecimalShape]
    decimal_non_null_fields: frozenset[str]
    watermark: dict[str, str | None]


@dataclass(frozen=True)
class ValidatedBusinessDelivery:
    """Private infrastructure handle; never returned in application results."""

    root: Path
    accepted_contract: AcceptedBusinessDataContract
    manifest: dict[str, Any]
    manifest_bytes: bytes
    descriptors: tuple[DatasetDescriptor, ...]
    scans: Mapping[str, DatasetScan]
    snapshot: BusinessDataSnapshot


class FileBusinessDataDeliveryResolver:
    """Resolve one exact directory from trusted tenant-to-delivery composition."""

    def __init__(self, *, tenant_delivery_directories: Mapping[str, Path]) -> None:
        mapping = {tenant: Path(path) for tenant, path in tenant_delivery_directories.items()}
        if not mapping or any(
            not tenant or not path.is_absolute() for tenant, path in mapping.items()
        ):
            raise BusinessDataDeliveryError()
        self._tenant_delivery_directories = MappingProxyType(mapping)

    def resolve(self, tenant_id: str) -> Path:
        directory = self._tenant_delivery_directories.get(tenant_id)
        if directory is None or directory.is_symlink() or not directory.is_dir():
            raise BusinessDataDeliveryError()
        try:
            resolved = directory.resolve(strict=True)
        except OSError:
            raise BusinessDataDeliveryError() from None
        if not resolved.is_dir():
            raise BusinessDataDeliveryError()
        return resolved


class BusinessDataDeliveryV2Consumer:
    """Independent consumer-side validator for SupportOps Delivery Format v2."""

    def __init__(
        self,
        *,
        delivery_resolver: FileBusinessDataDeliveryResolver,
        contract_catalog: BusinessDataContractCatalog,
    ) -> None:
        self._delivery_resolver = delivery_resolver
        self._contract_catalog = contract_catalog

    def validate(
        self,
        *,
        context: BusinessAnalyticsContext,
        catalog: EffectiveBusinessCatalog,
        policy: GovernedExecutionPolicy,
    ) -> ValidatedBusinessDelivery:
        try:
            return self._validate_untrusted_delivery(
                context=context,
                catalog=catalog,
                policy=policy,
            )
        except BusinessAnalyticsError:
            raise
        except Exception:
            raise BusinessDataIntegrityError() from None

    def _validate_untrusted_delivery(
        self,
        *,
        context: BusinessAnalyticsContext,
        catalog: EffectiveBusinessCatalog,
        policy: GovernedExecutionPolicy,
    ) -> ValidatedBusinessDelivery:
        root = self._delivery_resolver.resolve(context.tenant_id)
        accepted = self._contract_catalog.load_accepted_contract()
        self._validate_trusted_identity(context, catalog, accepted)

        manifest_path = root / "manifest.json"
        manifest_bytes = self._read_regular_file(
            manifest_path,
            policy.max_manifest_bytes,
        )
        if manifest_bytes.startswith(b"\xef\xbb\xbf"):
            raise BusinessDataIntegrityError()
        manifest = self._parse_json_object(manifest_bytes)
        if manifest_bytes != self._canonical_manifest_bytes(manifest):
            raise BusinessDataIntegrityError()

        descriptors = self._validate_manifest(
            root=root,
            manifest=manifest,
            context=context,
            accepted=accepted,
            policy=policy,
            manifest_size=len(manifest_bytes),
        )
        self._validate_directory_entries(root, descriptors)

        contract_datasets = accepted.contract.datasets
        scans: dict[str, DatasetScan] = {}
        known_primary_keys: dict[str, frozenset[tuple[str, ...]]] = {}
        for descriptor, dataset in zip(descriptors, contract_datasets, strict=True):
            scan = self._scan_dataset(
                root=root,
                descriptor=descriptor,
                dataset=dataset,
                known_primary_keys=known_primary_keys,
                expected_tenant_id=context.tenant_id,
                policy=policy,
            )
            scans[dataset.name] = scan
            known_primary_keys[dataset.name] = scan.primary_keys

        snapshot_metadata = manifest["snapshot"]
        self._validate_snapshot(
            snapshot=snapshot_metadata,
            manifest=manifest,
            descriptors=descriptors,
            scans=scans,
        )
        delivery_fingerprint = self._delivery_fingerprint(
            manifest_bytes,
            descriptors,
        )
        effective_names = tuple(dataset.logical_name for dataset in catalog.datasets)
        dataset_metadata = tuple(
            self._snapshot_dataset_metadata(scans[name]) for name in effective_names
        )
        snapshot = BusinessDataSnapshot(
            tenant_id=context.tenant_id,
            contract_name=accepted.contract.contract_name,
            contract_version=accepted.contract.version,
            contract_sha256=accepted.contract_sha256,
            delivery_fingerprint=delivery_fingerprint,
            snapshot_id=snapshot_metadata["snapshot_id"],
            currency=manifest["currency"],
            generated_at=manifest["generated_at"],
            consistency=snapshot_metadata["consistency"],
            started_at=snapshot_metadata["started_at"],
            completed_at=snapshot_metadata["completed_at"],
            effective_datasets=effective_names,
            datasets=dataset_metadata,
        )
        return ValidatedBusinessDelivery(
            root=root,
            accepted_contract=accepted,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            descriptors=descriptors,
            scans=MappingProxyType(scans),
            snapshot=snapshot,
        )

    def iter_materialization_rows(
        self,
        *,
        delivery: ValidatedBusinessDelivery,
        dataset: ExternalBusinessDataset,
        field_names: tuple[str, ...],
        policy: GovernedExecutionPolicy,
    ) -> Iterator[tuple[object, ...]]:
        """Read a second time, checking exact bytes while yielding typed fields."""

        scan = delivery.scans.get(dataset.name)
        if scan is None or any(
            name not in {field.name for field in dataset.fields}
            for name in field_names
        ):
            raise BusinessDataIntegrityError()
        path = delivery.root / scan.descriptor.file_name
        hasher = hashlib.sha256()
        count = 0
        fields_by_name = {field.name: field for field in dataset.fields}
        with self._open_regular_file(path, policy.max_input_bytes) as stream:
            while line := stream.readline(policy.max_record_bytes + 1):
                self._validate_line_boundary(line, count, policy)
                record = self._parse_json_object(line[:-1])
                if self._canonical_json_line(record) != line:
                    raise BusinessDataIntegrityError()
                self._validate_record_shape(record, dataset, delivery.snapshot.tenant_id)
                converted = {
                    name: self._convert_field(record[name], fields_by_name[name])
                    for name in field_names
                }
                values: list[object] = []
                for name in field_names:
                    value = converted[name]
                    if fields_by_name[name].logical_type is BusinessLogicalType.TIMESTAMP:
                        value = value.astimezone(UTC).replace(tzinfo=None)
                    values.append(value)
                hasher.update(line)
                count += 1
                yield tuple(values)

        if (
            count != scan.descriptor.row_count
            or hasher.hexdigest() != scan.descriptor.content_sha256
        ):
            raise BusinessDataIntegrityError()

    def _validate_trusted_identity(
        self,
        context: BusinessAnalyticsContext,
        catalog: EffectiveBusinessCatalog,
        accepted: AcceptedBusinessDataContract,
    ) -> None:
        if (
            context.contract_name != accepted.contract.contract_name
            or context.contract_version != accepted.contract.version
            or catalog.tenant_id != context.tenant_id
        ):
            raise BusinessDataTenantMismatchError()
        if (
            catalog.contract_name != accepted.contract.contract_name
            or catalog.contract_version != accepted.contract.version
            or catalog.contract_sha256 != accepted.contract_sha256
        ):
            raise BusinessDataIntegrityError()

    def _validate_manifest(
        self,
        *,
        root: Path,
        manifest: dict[str, Any],
        context: BusinessAnalyticsContext,
        accepted: AcceptedBusinessDataContract,
        policy: GovernedExecutionPolicy,
        manifest_size: int,
    ) -> tuple[DatasetDescriptor, ...]:
        if set(manifest) != MANIFEST_FIELDS:
            raise BusinessDataIntegrityError()
        contract = accepted.contract
        if (
            manifest["contract_name"] != contract.contract_name
            or manifest["contract_version"] != contract.version
            or manifest["export_format_version"] != EXPORT_FORMAT_VERSION
            or manifest["schema_sha256"] != accepted.contract_sha256
            or manifest["encoding"] != SUPPORTED_ENCODING
            or manifest["media_type"] != SUPPORTED_MEDIA_TYPE
        ):
            raise BusinessDataIntegrityError()
        if manifest["tenant_id"] != context.tenant_id:
            raise BusinessDataTenantMismatchError()
        self._parse_canonical_utc(manifest["generated_at"])
        descriptors_value = manifest["datasets"]
        if not isinstance(descriptors_value, list) or len(descriptors_value) != len(
            contract.datasets
        ):
            raise BusinessDataIntegrityError()

        descriptors: list[DatasetDescriptor] = []
        row_total = 0
        byte_total = manifest_size
        seen_names: set[str] = set()
        seen_files: set[str] = set()
        for descriptor_value, dataset in zip(
            descriptors_value,
            contract.datasets,
            strict=True,
        ):
            if not isinstance(descriptor_value, dict) or set(descriptor_value) != DESCRIPTOR_FIELDS:
                raise BusinessDataIntegrityError()
            name = descriptor_value["dataset_name"]
            file_name = descriptor_value["file_name"]
            row_count = descriptor_value["row_count"]
            content_sha256 = descriptor_value["content_sha256"]
            if (
                name != dataset.name
                or name not in DATASET_FILE_NAMES
                or file_name != DATASET_FILE_NAMES[name]
                or not self._safe_basename(file_name)
                or name in seen_names
                or file_name in seen_files
                or not isinstance(row_count, int)
                or isinstance(row_count, bool)
                or row_count < 0
                or row_count > policy.max_input_rows_per_dataset
                or not isinstance(content_sha256, str)
                or not SHA256_PATTERN.fullmatch(content_sha256)
            ):
                raise BusinessDataIntegrityError()
            size = self._regular_file_size(root / file_name)
            byte_total += size
            row_total += row_count
            if byte_total > policy.max_input_bytes:
                raise BusinessDataIntegrityError()
            seen_names.add(name)
            seen_files.add(file_name)
            descriptors.append(
                DatasetDescriptor(
                    dataset_name=name,
                    file_name=file_name,
                    row_count=row_count,
                    content_sha256=content_sha256,
                )
            )
        if row_total > policy.max_total_input_rows:
            raise BusinessDataIntegrityError()
        return tuple(descriptors)

    def _validate_directory_entries(
        self,
        root: Path,
        descriptors: tuple[DatasetDescriptor, ...],
    ) -> None:
        expected = {"manifest.json", *(item.file_name for item in descriptors)}
        try:
            entries = list(root.iterdir())
        except OSError:
            raise BusinessDataDeliveryError() from None
        if {entry.name for entry in entries} != expected:
            raise BusinessDataIntegrityError()
        if any(entry.is_symlink() or not entry.is_file() for entry in entries):
            raise BusinessDataIntegrityError()

    def _scan_dataset(
        self,
        *,
        root: Path,
        descriptor: DatasetDescriptor,
        dataset: ExternalBusinessDataset,
        known_primary_keys: Mapping[str, frozenset[tuple[str, ...]]],
        expected_tenant_id: str,
        policy: GovernedExecutionPolicy,
    ) -> DatasetScan:
        path = root / descriptor.file_name
        hasher = hashlib.sha256()
        primary_keys: set[tuple[str, ...]] = set()
        max_integer_digits = {
            field.name: 0
            for field in dataset.fields
            if field.logical_type.value == "decimal"
        }
        max_fractional_scale = {
            field.name: 0
            for field in dataset.fields
            if field.logical_type.value == "decimal"
        }
        decimal_non_null_fields: set[str] = set()
        watermark_field = self._watermark_field(dataset.name)
        watermark_value: str | None = None
        count = 0
        with self._open_regular_file(path, policy.max_input_bytes) as stream:
            while line := stream.readline(policy.max_record_bytes + 1):
                self._validate_line_boundary(line, count, policy)
                record = self._parse_json_object(line[:-1])
                if self._canonical_json_line(record) != line:
                    raise BusinessDataIntegrityError()
                self._validate_record_shape(record, dataset, expected_tenant_id)
                for field in dataset.fields:
                    value = record[field.name]
                    if value is None:
                        continue
                    if field.logical_type.value == "decimal":
                        decimal_non_null_fields.add(field.name)
                        integer_digits, scale = self._decimal_shape(value)
                        max_integer_digits[field.name] = max(
                            max_integer_digits[field.name],
                            integer_digits,
                        )
                        max_fractional_scale[field.name] = max(
                            max_fractional_scale[field.name],
                            scale,
                        )
                key = tuple(record[name] for name in dataset.primary_key.fields)
                if any(value is None or value == "" for value in key) or key in primary_keys:
                    raise BusinessDataIntegrityError()
                primary_keys.add(key)
                for relationship in dataset.relationships:
                    value = record[relationship.field]
                    if value is None and relationship.nullable:
                        continue
                    target_keys = known_primary_keys.get(relationship.target_dataset)
                    if target_keys is None:
                        raise BusinessDataIntegrityError()
                    target_key = tuple(
                        record["tenant_id"] if field_name == "tenant_id" else value
                        for field_name in relationship.target_primary_key
                    )
                    if target_key not in target_keys:
                        raise BusinessDataIntegrityError()
                if watermark_field is not None:
                    current = record[watermark_field]
                    if isinstance(current, str) and (
                        watermark_value is None or current > watermark_value
                    ):
                        watermark_value = current
                hasher.update(line)
                count += 1

        if count != descriptor.row_count or hasher.hexdigest() != descriptor.content_sha256:
            raise BusinessDataIntegrityError()
        decimal_shapes = {
            field_name: (
                self._make_decimal_shape(
                    max_integer_digits[field_name],
                    max_fractional_scale[field_name],
                )
                if field_name in decimal_non_null_fields
                else DecimalShape(precision=38, scale=18)
            )
            for field_name in max_integer_digits
        }
        return DatasetScan(
            descriptor=descriptor,
            primary_keys=frozenset(primary_keys),
            decimal_shapes=MappingProxyType(decimal_shapes),
            decimal_non_null_fields=frozenset(decimal_non_null_fields),
            watermark=self._watermark_metadata(dataset.name, watermark_value),
        )

    def _validate_snapshot(
        self,
        *,
        snapshot: object,
        manifest: Mapping[str, Any],
        descriptors: tuple[DatasetDescriptor, ...],
        scans: Mapping[str, DatasetScan],
    ) -> None:
        if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_FIELDS:
            raise BusinessDataIntegrityError()
        if snapshot["consistency"] not in ALLOWED_CONSISTENCY:
            raise BusinessDataIntegrityError()
        started_at = self._parse_canonical_utc(snapshot["started_at"])
        completed_at = self._parse_canonical_utc(snapshot["completed_at"])
        if completed_at < started_at:
            raise BusinessDataIntegrityError()
        snapshot_id = snapshot["snapshot_id"]
        if not isinstance(snapshot_id, str) or not SHA256_PATTERN.fullmatch(snapshot_id):
            raise BusinessDataIntegrityError()
        expected_id = self._snapshot_id(manifest, descriptors)
        if snapshot_id != expected_id:
            raise BusinessDataIntegrityError()
        observed_watermarks = {
            dataset_name: scan.watermark
            for dataset_name, scan in scans.items()
        }
        if snapshot["watermarks"] != observed_watermarks:
            raise BusinessDataIntegrityError()

    def _snapshot_dataset_metadata(
        self,
        scan: DatasetScan,
    ) -> BusinessDatasetSnapshotMetadata:
        watermark = scan.watermark
        return BusinessDatasetSnapshotMetadata(
            dataset_name=scan.descriptor.dataset_name,
            row_count=scan.descriptor.row_count,
            watermark_kind=watermark["kind"],
            watermark_field=watermark.get("field"),
            watermark_value=watermark.get("max_value"),
            unavailable_reason=watermark.get("reason"),
        )

    def _validate_record_shape(
        self,
        record: dict[str, Any],
        dataset: ExternalBusinessDataset,
        expected_tenant_id: str,
    ) -> None:
        expected_fields = {field.name: field for field in dataset.fields}
        if set(record) != set(expected_fields) or set(record) & FORBIDDEN_FIELD_NAMES:
            raise BusinessDataIntegrityError()
        if record.get("tenant_id") != expected_tenant_id:
            raise BusinessDataTenantMismatchError()
        for field_name, field in expected_fields.items():
            self._convert_field(record[field_name], field)

    def _convert_field(self, value: Any, field: ExternalBusinessField) -> object:
        if value is None:
            if field.nullable:
                return None
            raise BusinessDataIntegrityError()
        logical_type = field.logical_type.value
        if logical_type == "identifier":
            if not isinstance(value, str) or not value:
                raise BusinessDataIntegrityError()
            return value
        if logical_type == "string":
            if not isinstance(value, str):
                raise BusinessDataIntegrityError()
            return value
        if logical_type == "enum":
            if not isinstance(value, str) or value not in field.enum_values:
                raise BusinessDataIntegrityError()
            return value
        if logical_type == "decimal":
            if not isinstance(value, str) or not DECIMAL_PATTERN.fullmatch(value):
                raise BusinessDataIntegrityError()
            try:
                number = Decimal(value)
            except InvalidOperation:
                raise BusinessDataIntegrityError() from None
            if not number.is_finite():
                raise BusinessDataIntegrityError()
            return number
        if logical_type == "timestamp":
            return self._parse_canonical_utc(value)
        if logical_type == "boolean":
            if type(value) is not bool:
                raise BusinessDataIntegrityError()
            return value
        if logical_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise BusinessDataIntegrityError()
            return value
        raise BusinessDataIntegrityError()

    def _parse_canonical_utc(self, value: Any) -> datetime:
        if not isinstance(value, str) or not value.endswith("Z"):
            raise BusinessDataIntegrityError()
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError:
            raise BusinessDataIntegrityError() from None
        if (
            parsed.tzinfo is None
            or parsed.utcoffset() is None
            or parsed.utcoffset().total_seconds() != 0
        ):
            raise BusinessDataIntegrityError()
        timespec = "microseconds" if parsed.microsecond else "seconds"
        canonical = parsed.astimezone(UTC).isoformat(timespec=timespec).replace("+00:00", "Z")
        if canonical != value:
            raise BusinessDataIntegrityError()
        return parsed.astimezone(UTC)

    def _decimal_shape(self, value: Any) -> tuple[int, int]:
        if not isinstance(value, str) or not DECIMAL_PATTERN.fullmatch(value):
            raise BusinessDataIntegrityError()
        unsigned = value.lstrip("-")
        integer, _, fractional = unsigned.partition(".")
        significant_integer = integer.lstrip("0")
        integer_digits = len(significant_integer)
        scale = len(fractional)
        if integer_digits + scale > 38:
            raise BusinessDataIntegrityError()
        return integer_digits, scale

    def _make_decimal_shape(self, integer_digits: int, scale: int) -> DecimalShape:
        if scale > 38 or integer_digits + scale > 38:
            raise BusinessDataIntegrityError()
        precision = max(1, integer_digits + scale)
        return DecimalShape(precision=precision, scale=scale)

    def _watermark_field(self, dataset_name: str) -> str | None:
        if dataset_name == "tickets_v1":
            return "updated_at"
        if dataset_name == "ticket_events_v1":
            return "created_at"
        return None

    def _watermark_metadata(
        self,
        dataset_name: str,
        watermark_value: str | None,
    ) -> dict[str, str | None]:
        field = self._watermark_field(dataset_name)
        if field is not None:
            return {"kind": "max_timestamp", "field": field, "max_value": watermark_value}
        reason = (
            "orders_v1_has_no_reliable_change_timestamp"
            if dataset_name == "orders_v1"
            else "actions_v1_has_no_reliable_change_timestamp"
        )
        return {"kind": "unavailable", "reason": reason}

    def _snapshot_id(
        self,
        manifest: Mapping[str, Any],
        descriptors: tuple[DatasetDescriptor, ...],
    ) -> str:
        material = {
            "contract_sha256": manifest["schema_sha256"],
            "contract_version": manifest["contract_version"],
            "currency": manifest["currency"],
            "datasets": [
                {
                    "content_sha256": descriptor.content_sha256,
                    "dataset_name": descriptor.dataset_name,
                    "row_count": descriptor.row_count,
                }
                for descriptor in descriptors
            ],
            "tenant_id": manifest["tenant_id"],
        }
        return hashlib.sha256(self._canonical_manifest_bytes(material)).hexdigest()

    def _delivery_fingerprint(
        self,
        manifest_bytes: bytes,
        descriptors: tuple[DatasetDescriptor, ...],
    ) -> str:
        ordered_hashes = b"\n".join(item.content_sha256.encode("ascii") for item in descriptors)
        return hashlib.sha256(manifest_bytes + b"\n" + ordered_hashes).hexdigest()

    def _read_regular_file(self, path: Path, max_bytes: int) -> bytes:
        try:
            with self._open_regular_file(path, max_bytes) as stream:
                content = stream.read(max_bytes + 1)
        except BusinessDataIntegrityError:
            raise
        except OSError:
            raise BusinessDataDeliveryError() from None
        if len(content) > max_bytes:
            raise BusinessDataIntegrityError()
        return content

    def _open_regular_file(self, path: Path, max_bytes: int):
        if path.is_symlink():
            raise BusinessDataIntegrityError()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            raise BusinessDataDeliveryError() from None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                raise BusinessDataIntegrityError()
            return os.fdopen(descriptor, "rb")
        except Exception:
            os.close(descriptor)
            raise

    def _regular_file_size(self, path: Path) -> int:
        if path.is_symlink():
            raise BusinessDataIntegrityError()
        try:
            info = path.stat(follow_symlinks=False)
        except OSError:
            raise BusinessDataDeliveryError() from None
        if not stat.S_ISREG(info.st_mode):
            raise BusinessDataIntegrityError()
        return info.st_size

    def _validate_line_boundary(
        self,
        line: bytes,
        count: int,
        policy: GovernedExecutionPolicy,
    ) -> None:
        if len(line) > policy.max_record_bytes or not line.endswith(b"\n") or line == b"\n":
            raise BusinessDataIntegrityError()
        if count == 0 and line.startswith(b"\xef\xbb\xbf"):
            raise BusinessDataIntegrityError()

    def _parse_json_object(self, content: bytes) -> dict[str, Any]:
        try:
            value = json.loads(
                content.decode("utf-8"),
                object_pairs_hook=self._reject_duplicate_keys,
                parse_constant=self._reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            raise BusinessDataIntegrityError() from None
        if not isinstance(value, dict):
            raise BusinessDataIntegrityError()
        return value

    def _reject_duplicate_keys(self, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def _reject_constant(self, value: str) -> None:
        raise ValueError("invalid JSON constant")

    def _canonical_json_line(self, value: dict[str, Any]) -> bytes:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")

    def _canonical_manifest_bytes(self, value: object) -> bytes:
        return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )

    def _safe_basename(self, value: Any) -> bool:
        return (
            isinstance(value, str)
            and bool(value)
            and value == Path(value).name
            and "/" not in value
            and "\\" not in value
            and ":" not in value
        )
