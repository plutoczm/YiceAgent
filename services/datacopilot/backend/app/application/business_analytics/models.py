from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.application.text2sql.models import SQLEngine


class BusinessAnalyticsModel(BaseModel):
    """Immutable business analytics contract with a closed field set."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


LogicalDatasetName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
]
TenantIdentity = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"),
]
ContractName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9._-]*$"),
]
ContractVersion = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$"),
]
SafeIdentity = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"),
]
CatalogFingerprint = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]*$"),
]
ContractSha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
FieldName = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"),
]


class BusinessLogicalType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TIMESTAMP = "timestamp"
    JSON = "json"
    IDENTIFIER = "identifier"
    ENUM = "enum"
    UNKNOWN = "unknown"


class BusinessFieldClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class BusinessClassificationDefinition(BusinessAnalyticsModel):
    classification: BusinessFieldClassification
    meaning: str = Field(min_length=1, max_length=2000)


class BusinessAnalyticsStatus(StrEnum):
    PREPARED = "prepared"


class BusinessRelationshipCardinality(StrEnum):
    MANY_TO_ONE = "many-to-one"


class BusinessDatasetStabilityLevel(StrEnum):
    STABLE = "stable"


class BusinessDatasetPrimaryKey(BusinessAnalyticsModel):
    fields: tuple[FieldName, ...] = Field(min_length=1)
    semantic_meaning: str = Field(min_length=1, max_length=1000)

    @field_validator("fields")
    @classmethod
    def require_unique_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("primary key fields must be unique")
        return value


class BusinessDatasetStability(BusinessAnalyticsModel):
    level: BusinessDatasetStabilityLevel
    compatibility: str = Field(min_length=1, max_length=1000)


class BusinessFieldReference(BusinessAnalyticsModel):
    target_dataset: LogicalDatasetName
    target_fields: tuple[FieldName, ...] = Field(min_length=1)

    @field_validator("target_fields")
    @classmethod
    def require_unique_target_fields(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("field reference target fields must be unique")
        return value


class BusinessAnalyticsRequest(BusinessAnalyticsModel):
    """Untrusted intent and preferences supplied by a user or agent."""

    question: str = Field(min_length=1, max_length=8000)
    engine: SQLEngine = SQLEngine.HIVE
    requested_datasets: tuple[LogicalDatasetName, ...] = ()

    @field_validator("requested_datasets")
    @classmethod
    def require_unique_datasets(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("requested_datasets must not contain duplicates")
        return value


class BusinessAnalyticsContext(BusinessAnalyticsModel):
    """Trusted values injected by server-side application composition."""

    request_id: SafeIdentity
    tenant_id: TenantIdentity
    contract_name: ContractName
    contract_version: ContractVersion
    allowed_datasets: tuple[LogicalDatasetName, ...] = Field(min_length=1)
    authenticated_subject_fingerprint: ContractSha256 | None = None
    authorization_grant_id: ContractSha256 | None = None
    entrypoint: Literal["api", "agent", "internal"] = "internal"

    @field_validator("allowed_datasets")
    @classmethod
    def require_unique_datasets(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("allowed_datasets must not contain duplicates")
        return value


class BusinessField(BusinessAnalyticsModel):
    name: FieldName
    logical_type: BusinessLogicalType
    nullable: bool
    meaning: str = Field(min_length=1, max_length=1000)
    classification: BusinessFieldClassification
    description: str | None = Field(default=None, max_length=2000)
    enum_values: tuple[str, ...] | None = None
    unit: str | None = Field(default=None, max_length=128)
    serialization: str | None = Field(default=None, max_length=1000)
    currency_semantics: str | None = Field(default=None, max_length=2000)
    timestamp_semantics: str | None = Field(default=None, max_length=2000)
    identifier_semantics: str | None = Field(default=None, max_length=2000)
    references: BusinessFieldReference | None = None

    @field_validator("enum_values")
    @classmethod
    def require_unique_enum_values(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is not None and (
            not value or any(not item.strip() for item in value)
        ):
            raise ValueError("enum_values must contain non-empty strings")
        if value is not None and len(value) != len(set(value)):
            raise ValueError("enum_values must not contain duplicates")
        return value

    @model_validator(mode="after")
    def require_enum_values_for_enum_type(self) -> "BusinessField":
        if (self.logical_type is BusinessLogicalType.ENUM) != (
            self.enum_values is not None
        ):
            raise ValueError("enum logical types require enum_values only")
        return self


class BusinessDatasetRelationship(BusinessAnalyticsModel):
    target_dataset: LogicalDatasetName
    source_fields: tuple[FieldName, ...] = Field(min_length=1)
    target_fields: tuple[FieldName, ...] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=1000)
    name: FieldName | None = None
    cardinality: BusinessRelationshipCardinality | None = None
    nullable: bool | None = None
    meaning: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_matching_key_lengths(self) -> "BusinessDatasetRelationship":
        if len(self.source_fields) != len(self.target_fields):
            raise ValueError("relationship key tuples must have equal lengths")
        if len(self.source_fields) != len(set(self.source_fields)):
            raise ValueError("relationship source fields must be unique")
        if len(self.target_fields) != len(set(self.target_fields)):
            raise ValueError("relationship target fields must be unique")
        return self


class BusinessDataset(BusinessAnalyticsModel):
    logical_name: LogicalDatasetName
    description: str = Field(min_length=1, max_length=2000)
    grain: str = Field(min_length=1, max_length=1000)
    fields: tuple[BusinessField, ...] = Field(min_length=1)
    relationships: tuple[BusinessDatasetRelationship, ...] = ()
    primary_key: BusinessDatasetPrimaryKey | None = None
    classification: BusinessFieldClassification | None = None
    stability: BusinessDatasetStability | None = None

    @field_validator("fields")
    @classmethod
    def require_unique_fields(
        cls,
        value: tuple[BusinessField, ...],
    ) -> tuple[BusinessField, ...]:
        names = [field.name.lower() for field in value]
        if len(names) != len(set(names)):
            raise ValueError("dataset field names must be unique")
        return value

    @model_validator(mode="after")
    def require_primary_key_fields(self) -> "BusinessDataset":
        if self.primary_key is not None:
            field_names = {field.name for field in self.fields}
            if not set(self.primary_key.fields).issubset(field_names):
                raise ValueError("primary key field is unavailable")
        return self


class BusinessCatalogSnapshot(BusinessAnalyticsModel):
    """Typed, trusted catalog metadata returned by a BusinessCatalogPort."""

    tenant_id: TenantIdentity
    contract_name: ContractName
    contract_version: ContractVersion
    contract_sha256: ContractSha256
    catalog_fingerprint: CatalogFingerprint
    datasets: tuple[BusinessDataset, ...] = Field(min_length=1)
    classification_definitions: tuple[BusinessClassificationDefinition, ...] = ()

    @model_validator(mode="after")
    def require_unique_and_resolvable_datasets(self) -> "BusinessCatalogSnapshot":
        classifications = [
            definition.classification for definition in self.classification_definitions
        ]
        if len(classifications) != len(set(classifications)):
            raise ValueError("catalog classification definitions must be unique")
        names = [dataset.logical_name for dataset in self.datasets]
        if len(names) != len(set(names)):
            raise ValueError("catalog dataset names must be unique")
        datasets_by_name = {dataset.logical_name: dataset for dataset in self.datasets}
        for dataset in self.datasets:
            source_fields = {field.name for field in dataset.fields}
            for business_field in dataset.fields:
                reference = business_field.references
                if reference is None:
                    continue
                target = datasets_by_name.get(reference.target_dataset)
                if target is None:
                    raise ValueError("field reference target is unavailable")
                if not set(reference.target_fields).issubset(
                    {field.name for field in target.fields}
                ):
                    raise ValueError("field reference target field is unavailable")
            for relation in dataset.relationships:
                target = datasets_by_name.get(relation.target_dataset)
                if target is None:
                    raise ValueError("catalog relationship target is unavailable")
                target_fields = {field.name for field in target.fields}
                if not set(relation.source_fields).issubset(source_fields):
                    raise ValueError("catalog relationship source field is unavailable")
                if not set(relation.target_fields).issubset(target_fields):
                    raise ValueError("catalog relationship target field is unavailable")
        return self


class BusinessAnalyticsResult(BusinessAnalyticsModel):
    """Safe outcome of D1 catalog preparation; it contains no SQL or rows."""

    request_id: SafeIdentity
    contract_name: ContractName
    contract_version: ContractVersion
    catalog_fingerprint: CatalogFingerprint
    resolved_datasets: tuple[LogicalDatasetName, ...] = Field(min_length=1)
    status: BusinessAnalyticsStatus = BusinessAnalyticsStatus.PREPARED
    catalog_snapshot: BusinessCatalogSnapshot = Field(exclude=True)
