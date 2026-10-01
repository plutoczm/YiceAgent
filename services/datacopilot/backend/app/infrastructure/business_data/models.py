import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)


class ExternalContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


ContractDatasetName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
]
ContractFieldName = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z_][a-z0-9_]*$"),
]
Sha256Digest = Annotated[
    str,
    Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$"),
]


class ExternalClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ExternalLogicalType(StrEnum):
    IDENTIFIER = "identifier"
    STRING = "string"
    DECIMAL = "decimal"
    ENUM = "enum"
    TIMESTAMP = "timestamp"
    BOOLEAN = "boolean"


class ExternalCardinality(StrEnum):
    MANY_TO_ONE = "many-to-one"


class ExternalClassificationDefinitions(ExternalContractModel):
    public: str = Field(min_length=1, max_length=1000)
    internal: str = Field(min_length=1, max_length=1000)
    confidential: str = Field(min_length=1, max_length=2000)
    restricted: str = Field(min_length=1, max_length=2000)


class ExternalCompatibility(ExternalContractModel):
    v1_minor_allows: tuple[str, ...] = Field(min_length=1)
    new_major_required_for: tuple[str, ...] = Field(min_length=1)

    @field_validator("v1_minor_allows", "new_major_required_for")
    @classmethod
    def require_unique_rules(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not rule for rule in value) or len(value) != len(set(value)):
            raise ValueError("compatibility rules must be unique non-empty strings")
        return value


class ExternalPrimaryKey(ExternalContractModel):
    fields: tuple[ContractFieldName, ...] = Field(min_length=1)
    semantic_meaning: str = Field(min_length=1, max_length=2000)

    @field_validator("fields")
    @classmethod
    def require_unique_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("primary key fields must be unique")
        return value


class ExternalDatasetStability(ExternalContractModel):
    level: Literal["stable"]
    compatibility: str = Field(min_length=1, max_length=1000)


class ExternalBusinessField(ExternalContractModel):
    name: ContractFieldName
    description: str = Field(min_length=1, max_length=2000)
    logical_type: ExternalLogicalType
    nullable: StrictBool
    semantic_meaning: str = Field(min_length=1, max_length=2000)
    sensitivity: ExternalClassification
    identifier_semantics: str | None = Field(default=None, max_length=2000)
    enum_values: tuple[str, ...] | None = None
    unit: str | None = Field(default=None, max_length=128)
    serialization: str | None = Field(default=None, max_length=1000)
    currency_semantics: str | None = Field(default=None, max_length=2000)
    timestamp_semantics: str | None = Field(default=None, max_length=2000)
    references: str | None = Field(default=None, max_length=256)

    @field_validator("enum_values")
    @classmethod
    def require_unique_enum_values(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is not None and (
            not value
            or any(not item.strip() for item in value)
            or len(value) != len(set(value))
        ):
            raise ValueError("enum_values must be unique non-empty strings")
        return value

    @model_validator(mode="after")
    def require_enum_metadata(self) -> "ExternalBusinessField":
        if (self.logical_type is ExternalLogicalType.ENUM) != (
            self.enum_values is not None
        ):
            raise ValueError("enum logical types require enum_values only")
        return self


class ExternalRelationship(ExternalContractModel):
    field: ContractFieldName
    target_dataset: ContractDatasetName
    target_primary_key: tuple[ContractFieldName, ...] = Field(min_length=1)
    cardinality: ExternalCardinality
    nullable: StrictBool
    semantic_meaning: str = Field(min_length=1, max_length=2000)

    @field_validator("target_primary_key")
    @classmethod
    def require_unique_target_fields(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("relationship target key fields must be unique")
        return value


class ExternalBusinessDataset(ExternalContractModel):
    name: ContractDatasetName
    description: str = Field(min_length=1, max_length=2000)
    grain: str = Field(min_length=1, max_length=2000)
    primary_key: ExternalPrimaryKey
    relationships: tuple[ExternalRelationship, ...]
    sensitivity: ExternalClassification
    stability: ExternalDatasetStability
    fields: tuple[ExternalBusinessField, ...] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def require_unique_field_names(
        cls,
        value: tuple[ExternalBusinessField, ...],
    ) -> tuple[ExternalBusinessField, ...]:
        names = [field.name.lower() for field in value]
        if len(names) != len(set(names)):
            raise ValueError("dataset field names must be unique")
        return value

    @model_validator(mode="after")
    def require_primary_key_fields(self) -> "ExternalBusinessDataset":
        field_names = {field.name for field in self.fields}
        if not set(self.primary_key.fields).issubset(field_names):
            raise ValueError("dataset primary key field is unavailable")
        if self.primary_key.fields[0] != "tenant_id":
            raise ValueError("dataset primary key must begin with tenant_id")
        relation_names = [
            (relationship.field, relationship.target_dataset)
            for relationship in self.relationships
        ]
        if len(relation_names) != len(set(relation_names)):
            raise ValueError("dataset relationships must be unique")
        for relationship in self.relationships:
            source_field = next(
                field for field in self.fields if field.name == relationship.field
            ) if relationship.field in field_names else None
            if source_field is None:
                raise ValueError("relationship source field is unavailable")
            if source_field.nullable is not relationship.nullable:
                raise ValueError("relationship nullability does not match its field")
        return self


class ExternalBusinessDataContract(ExternalContractModel):
    contract_name: Annotated[
        str,
        Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$"),
    ]
    version: Literal["v1"]
    owner: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    data_classifications: ExternalClassificationDefinitions
    compatibility: ExternalCompatibility
    datasets: tuple[ExternalBusinessDataset, ...] = Field(min_length=1)

    @field_validator("datasets")
    @classmethod
    def require_unique_dataset_names(
        cls,
        value: tuple[ExternalBusinessDataset, ...],
    ) -> tuple[ExternalBusinessDataset, ...]:
        names = [dataset.name for dataset in value]
        if len(names) != len(set(names)):
            raise ValueError("contract dataset names must be unique")
        return value

    @model_validator(mode="after")
    def validate_relationship_targets(self) -> "ExternalBusinessDataContract":
        datasets = {dataset.name: dataset for dataset in self.datasets}
        for dataset in self.datasets:
            field_map = {field.name: field for field in dataset.fields}
            for relationship in dataset.relationships:
                target = datasets.get(relationship.target_dataset)
                if target is None:
                    raise ValueError("relationship target dataset is unavailable")
                target_fields = {field.name for field in target.fields}
                if relationship.target_primary_key != target.primary_key.fields:
                    raise ValueError(
                        "relationship target key does not match dataset key"
                    )
                if not set(relationship.target_primary_key).issubset(target_fields):
                    raise ValueError("relationship target field is unavailable")

            for field in dataset.fields:
                if field.references is None:
                    continue
                referenced_dataset, referenced_fields = parse_field_reference(
                    field.references
                )
                target = datasets.get(referenced_dataset)
                if target is None:
                    raise ValueError("field reference target dataset is unavailable")
                if referenced_fields != target.primary_key.fields:
                    raise ValueError(
                        "field reference target key does not match dataset key"
                    )
                matching_relationship = any(
                    relationship.field == field.name
                    and relationship.target_dataset == referenced_dataset
                    and relationship.target_primary_key == referenced_fields
                    for relationship in dataset.relationships
                )
                if not matching_relationship:
                    raise ValueError("field reference has no matching relationship")
        return self


class ContractAcceptance(ExternalContractModel):
    contract_name: Annotated[
        str,
        Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$"),
    ]
    accepted_version: Literal["v1"]
    contract_sha256: Sha256Digest
    source_owner: str = Field(min_length=1, max_length=128)
    compatibility_policy: Literal["exact-artifact-sha256"]


def parse_field_reference(value: str) -> tuple[str, tuple[str, ...]]:
    match = re.fullmatch(
        r"(?P<dataset>[a-z][a-z0-9_]*)\."
        r"\((?P<fields>[a-z_][a-z0-9_]*(?:\s*,\s*[a-z_][a-z0-9_]*)*)\)",
        value,
    )
    if match is None:
        raise ValueError("field reference has an invalid format")
    fields = tuple(part.strip() for part in match.group("fields").split(","))
    if len(fields) != len(set(fields)):
        raise ValueError("field reference fields must be unique")
    return match.group("dataset"), fields
