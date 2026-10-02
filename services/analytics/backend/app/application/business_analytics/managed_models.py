from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from backend.app.application.business_analytics.models import (
    BusinessAnalyticsModel,
    BusinessDataset,
    BusinessFieldClassification,
    CatalogFingerprint,
    ContractSha256,
    ContractName,
    ContractVersion,
    LogicalDatasetName,
    SafeIdentity,
    TenantIdentity,
)
from backend.app.application.text2sql.models import SQLEngine


class ManagedAnalyticsPolicy(BusinessAnalyticsModel):
    """Server-controlled field and resource policy for managed generation."""

    allowed_classifications: tuple[BusinessFieldClassification, ...] = Field(
        min_length=1,
        default=(
            BusinessFieldClassification.PUBLIC,
            BusinessFieldClassification.INTERNAL,
            BusinessFieldClassification.CONFIDENTIAL,
        ),
    )
    max_rows: int = Field(default=100, ge=1, le=500)
    max_schema_chars: int = Field(default=32_768, ge=256, le=32_768)
    max_sql_chars: int = Field(default=16_384, ge=256, le=16_384)

    @field_validator("allowed_classifications")
    @classmethod
    def require_unique_classifications(
        cls,
        value: tuple[BusinessFieldClassification, ...],
    ) -> tuple[BusinessFieldClassification, ...]:
        if len(value) != len(set(value)):
            raise ValueError("allowed_classifications must not contain duplicates")
        return value


class EffectiveBusinessCatalog(BusinessAnalyticsModel):
    """The one policy-filtered catalog view used by renderer and authorizer."""

    request_id: SafeIdentity
    tenant_id: TenantIdentity
    contract_name: ContractName
    contract_version: ContractVersion
    contract_sha256: ContractSha256
    catalog_fingerprint: CatalogFingerprint
    engine: SQLEngine
    allowed_classifications: tuple[BusinessFieldClassification, ...]
    datasets: tuple[BusinessDataset, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_effective_scope(self) -> "EffectiveBusinessCatalog":
        names = [dataset.logical_name for dataset in self.datasets]
        if len(names) != len(set(names)):
            raise ValueError("effective dataset names must be unique")
        if len(self.allowed_classifications) != len(set(self.allowed_classifications)):
            raise ValueError("effective classifications must be unique")
        allowed_classes = set(self.allowed_classifications)
        datasets = {dataset.logical_name: dataset for dataset in self.datasets}
        for dataset in self.datasets:
            if any(field.classification not in allowed_classes for field in dataset.fields):
                raise ValueError("effective catalog includes a disallowed field")
            if dataset.primary_key is not None and not set(
                dataset.primary_key.fields
            ).issubset({field.name for field in dataset.fields}):
                raise ValueError("effective primary key contains a filtered field")
            for relationship in dataset.relationships:
                target = datasets.get(relationship.target_dataset)
                if target is None:
                    raise ValueError("effective relationship target is unavailable")
                if not set(relationship.source_fields).issubset(
                    {field.name for field in dataset.fields}
                ) or not set(relationship.target_fields).issubset(
                    {field.name for field in target.fields}
                ):
                    raise ValueError("effective relationship references a filtered field")
            for field in dataset.fields:
                if field.references is not None:
                    target = datasets.get(field.references.target_dataset)
                    if target is None or not set(field.references.target_fields).issubset(
                        {target_field.name for target_field in target.fields}
                    ):
                        raise ValueError("effective field reference target is unavailable")
        return self


class ManagedSQLGenerationRequest(BusinessAnalyticsModel):
    """Only pipeline-resolved intent, engine, and schema reach a generator."""

    question: str = Field(min_length=1, max_length=8000)
    engine: SQLEngine
    rendered_schema: str = Field(min_length=1, max_length=32_768)


class ManagedTokenUsage(BusinessAnalyticsModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class ManagedGenericValidationIssue(BusinessAnalyticsModel):
    code: str = Field(min_length=1, max_length=128)
    severity: Literal["error", "warning"]
    object_name: str | None = Field(default=None, max_length=256)


class ManagedGenericValidation(BusinessAnalyticsModel):
    is_valid: bool
    issues: tuple[ManagedGenericValidationIssue, ...] = ()


class ManagedSQLDraft(BusinessAnalyticsModel):
    """Untrusted SQL candidate returned by a generator adapter."""

    candidate_sql: Annotated[
        str,
        StringConstraints(
            min_length=1,
            max_length=256_000,
            strip_whitespace=False,
        ),
    ]
    generic_validation: ManagedGenericValidation
    token_usage: ManagedTokenUsage = Field(default_factory=ManagedTokenUsage)
    model_explanation: str | None = Field(default=None, max_length=8000)


class ManagedSQLReferencedField(BusinessAnalyticsModel):
    dataset_name: LogicalDatasetName
    field_name: str = Field(min_length=1, max_length=128)


class ManagedSQLValidationSummary(BusinessAnalyticsModel):
    generic_safety_valid: bool
    parse_valid: bool
    read_only: bool
    dataset_scope_valid: bool
    column_scope_valid: bool
    classification_policy_valid: bool
    wildcard_policy_valid: bool
    row_limit_valid: bool


class ManagedBusinessSQLStatus(StrEnum):
    READY = "ready"


class ManagedBusinessSQLPlan(BusinessAnalyticsModel):
    """Authorized logical SQL plan; it does not claim a query was executed."""

    request_id: SafeIdentity
    tenant_id: TenantIdentity
    contract_name: ContractName
    contract_version: ContractVersion
    contract_sha256: ContractSha256
    catalog_fingerprint: CatalogFingerprint
    engine: SQLEngine
    effective_datasets: tuple[LogicalDatasetName, ...] = Field(min_length=1)
    referenced_datasets: tuple[LogicalDatasetName, ...] = ()
    referenced_fields: tuple[ManagedSQLReferencedField, ...] = ()
    generated_sql: str = Field(min_length=1, max_length=16_384)
    validation: ManagedSQLValidationSummary
    max_rows: int = Field(ge=1, le=500)
    allowed_classifications: tuple[BusinessFieldClassification, ...]
    token_usage: ManagedTokenUsage = Field(default_factory=ManagedTokenUsage)
    model_explanation: str | None = Field(default=None, max_length=8000)
    status: ManagedBusinessSQLStatus = ManagedBusinessSQLStatus.READY
    effective_catalog: EffectiveBusinessCatalog = Field(exclude=True)

    @model_validator(mode="after")
    def references_must_be_within_effective_scope(self) -> "ManagedBusinessSQLPlan":
        effective = set(self.effective_datasets)
        if not set(self.referenced_datasets).issubset(effective):
            raise ValueError("referenced datasets exceed effective scope")
        if any(field.dataset_name not in effective for field in self.referenced_fields):
            raise ValueError("referenced fields exceed effective scope")
        if len(self.allowed_classifications) != len(set(self.allowed_classifications)):
            raise ValueError("plan classifications must be unique")
        catalog = self.effective_catalog
        if (
            catalog.tenant_id != self.tenant_id
            or catalog.contract_name != self.contract_name
            or catalog.contract_version != self.contract_version
            or catalog.contract_sha256 != self.contract_sha256
            or catalog.catalog_fingerprint != self.catalog_fingerprint
            or catalog.engine is not self.engine
            or tuple(dataset.logical_name for dataset in catalog.datasets)
            != self.effective_datasets
            or catalog.allowed_classifications != self.allowed_classifications
        ):
            raise ValueError("plan metadata does not match its effective catalog")
        return self
