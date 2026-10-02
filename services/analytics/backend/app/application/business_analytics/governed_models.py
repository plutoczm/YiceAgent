from enum import StrEnum
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from backend.app.application.business_analytics.managed_models import (
    ManagedSQLReferencedField,
    ManagedTokenUsage,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsModel,
    BusinessFieldClassification,
    CatalogFingerprint,
    ContractName,
    ContractSha256,
    ContractVersion,
    LogicalDatasetName,
    SafeIdentity,
    TenantIdentity,
)
from backend.app.application.text2sql.models import SQLEngine


class GovernedExecutionPolicy(BusinessAnalyticsModel):
    """Server-controlled delivery, query and result budgets for D4."""

    max_manifest_bytes: int = Field(default=1_048_576, ge=1024, le=1_048_576)
    max_record_bytes: int = Field(default=1_048_576, ge=256, le=1_048_576)
    max_input_bytes: int = Field(default=67_108_864, ge=1024, le=67_108_864)
    max_input_rows_per_dataset: int = Field(default=100_000, ge=1, le=100_000)
    max_total_input_rows: int = Field(default=200_000, ge=1, le=200_000)
    materialization_batch_rows: int = Field(default=500, ge=1, le=5000)
    max_sql_chars: int = Field(default=16_384, ge=256, le=16_384)
    max_execution_seconds: float = Field(default=5.0, gt=0, le=60)
    cancel_grace_seconds: float = Field(default=2.0, gt=0, le=10)
    max_result_rows: int = Field(default=100, ge=1, le=500)
    max_result_bytes: int = Field(default=1_048_576, ge=128, le=1_048_576)
    max_result_columns: int = Field(default=100, ge=1, le=100)
    max_result_cell_bytes: int = Field(default=65_536, ge=64, le=65_536)
    duckdb_memory_limit_bytes: int = Field(
        default=268_435_456,
        ge=16_777_216,
        le=268_435_456,
    )
    duckdb_threads: int = Field(default=1, ge=1, le=1)


class BusinessAnalyticsExecutionStatus(StrEnum):
    EXECUTED = "executed"


class BusinessAnalyticsAuditStatus(StrEnum):
    STARTED = "started"
    EXECUTED = "executed"
    FAILED = "failed"


WatermarkKind = Literal["max_timestamp", "unavailable"]
SnapshotConsistency = Literal[
    "postgresql_repeatable_read_read_only",
    "sqlite_single_connection_read_transaction",
    "single_connection_read_transaction",
]
JsonScalar = StrictStr | StrictInt | StrictBool | None


class BusinessDatasetSnapshotMetadata(BusinessAnalyticsModel):
    dataset_name: LogicalDatasetName
    row_count: int = Field(ge=0)
    watermark_kind: WatermarkKind
    watermark_field: str | None = Field(default=None, max_length=128)
    watermark_value: str | None = Field(default=None, max_length=64)
    unavailable_reason: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_watermark_shape(self) -> "BusinessDatasetSnapshotMetadata":
        if self.watermark_kind == "max_timestamp":
            if not self.watermark_field or self.unavailable_reason is not None:
                raise ValueError("max-timestamp watermark metadata is incomplete")
        elif (
            self.watermark_field is not None
            or self.watermark_value is not None
            or not self.unavailable_reason
        ):
            raise ValueError("unavailable watermark metadata is inconsistent")
        return self


class BusinessDataSnapshot(BusinessAnalyticsModel):
    """Validated Delivery v2 identity and scope; contains no filesystem path."""

    tenant_id: TenantIdentity
    contract_name: ContractName
    contract_version: ContractVersion
    contract_sha256: ContractSha256
    delivery_fingerprint: ContractSha256
    snapshot_id: ContractSha256
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    generated_at: str = Field(min_length=20, max_length=64)
    consistency: SnapshotConsistency
    started_at: str = Field(min_length=20, max_length=64)
    completed_at: str = Field(min_length=20, max_length=64)
    effective_datasets: tuple[LogicalDatasetName, ...] = Field(min_length=1)
    datasets: tuple[BusinessDatasetSnapshotMetadata, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_effective_dataset_metadata(self) -> "BusinessDataSnapshot":
        names = tuple(dataset.dataset_name for dataset in self.datasets)
        if len(names) != len(set(names)) or set(names) != set(self.effective_datasets):
            raise ValueError("snapshot dataset metadata must match effective scope")
        if len(self.effective_datasets) != len(set(self.effective_datasets)):
            raise ValueError("effective snapshot datasets must be unique")
        return self


class GovernedResultColumn(BusinessAnalyticsModel):
    name: str = Field(min_length=1, max_length=256)
    data_type: str = Field(min_length=1, max_length=128)


class BusinessAnalyticsExecutionData(BusinessAnalyticsModel):
    """Internal execution-port outcome before the application marks it EXECUTED."""

    snapshot: BusinessDataSnapshot
    source_engine: SQLEngine
    execution_engine: Literal["duckdb"]
    referenced_datasets: tuple[LogicalDatasetName, ...]
    referenced_fields: tuple[ManagedSQLReferencedField, ...] = ()
    columns: tuple[GovernedResultColumn, ...]
    rows: tuple[tuple[JsonScalar, ...], ...]
    row_count: int = Field(ge=0)
    result_classification: BusinessFieldClassification
    duration_ms: int = Field(ge=0)
    query_fingerprint: ContractSha256
    token_usage: ManagedTokenUsage
    source_plan_reauthorized: bool
    translated_sql_reauthorized: bool

    @model_validator(mode="after")
    def validate_bounded_row_shape(self) -> "BusinessAnalyticsExecutionData":
        if self.row_count != len(self.rows):
            raise ValueError("row_count must equal the returned row count")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("each result row must match the result column count")
        if len(self.referenced_datasets) != len(set(self.referenced_datasets)):
            raise ValueError("referenced datasets must be unique")
        return self


class GovernedBusinessAnalyticsResult(BusinessAnalyticsModel):
    """Bounded in-memory execution result, never a claim of durable storage."""

    status: BusinessAnalyticsExecutionStatus = BusinessAnalyticsExecutionStatus.EXECUTED
    request_id: SafeIdentity
    tenant_id: TenantIdentity
    contract_name: ContractName
    contract_version: ContractVersion
    contract_sha256: ContractSha256
    catalog_fingerprint: CatalogFingerprint
    snapshot: BusinessDataSnapshot
    source_engine: SQLEngine
    execution_engine: Literal["duckdb"]
    effective_datasets: tuple[LogicalDatasetName, ...] = Field(min_length=1)
    referenced_datasets: tuple[LogicalDatasetName, ...] = ()
    referenced_fields: tuple[ManagedSQLReferencedField, ...] = ()
    columns: tuple[GovernedResultColumn, ...]
    rows: tuple[tuple[JsonScalar, ...], ...]
    row_count: int = Field(ge=0)
    result_classification: BusinessFieldClassification
    duration_ms: int = Field(ge=0)
    query_fingerprint: ContractSha256
    token_usage: ManagedTokenUsage
    source_plan_reauthorized: bool
    translated_sql_reauthorized: bool

    @model_validator(mode="after")
    def validate_result_identity_and_shape(self) -> "GovernedBusinessAnalyticsResult":
        if (
            self.snapshot.tenant_id != self.tenant_id
            or self.snapshot.contract_name != self.contract_name
            or self.snapshot.contract_version != self.contract_version
            or self.snapshot.contract_sha256 != self.contract_sha256
            or self.snapshot.effective_datasets != self.effective_datasets
        ):
            raise ValueError("result identity must match its validated snapshot")
        if self.row_count != len(self.rows):
            raise ValueError("row_count must equal the returned row count")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("each result row must match the result column count")
        return self


class BusinessAnalyticsAuditEvent(BusinessAnalyticsModel):
    """Safe metadata-only event; the closed schema excludes SQL and row values."""

    request_id: SafeIdentity
    tenant_id: TenantIdentity
    authenticated_subject_fingerprint: ContractSha256 | None = None
    authorization_grant_id: ContractSha256 | None = None
    entrypoint: Literal["api", "agent", "internal"] = "internal"
    contract_name: ContractName
    contract_version: ContractVersion
    catalog_fingerprint: CatalogFingerprint | None = None
    delivery_fingerprint: ContractSha256 | None = None
    query_fingerprint: ContractSha256 | None = None
    source_engine: SQLEngine | None = None
    execution_engine: Literal["duckdb"] | None = None
    status: BusinessAnalyticsAuditStatus
    referenced_datasets: tuple[LogicalDatasetName, ...] = ()
    row_count: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, max_length=128)
