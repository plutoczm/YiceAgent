import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from pydantic import ValidationError

from backend.app.application.business_analytics.errors import (
    BusinessAnalyticsContextError,
    BusinessCatalogMismatchError,
    BusinessScopeViolationError,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessCatalogSnapshot,
    BusinessClassificationDefinition,
    BusinessDataset,
    BusinessDatasetPrimaryKey,
    BusinessDatasetRelationship,
    BusinessDatasetStability,
    BusinessField,
    BusinessFieldClassification,
    BusinessFieldReference,
    BusinessLogicalType,
    BusinessRelationshipCardinality,
)
from backend.app.infrastructure.business_data.errors import (
    BusinessContractFormatError,
    BusinessContractIdentityError,
    BusinessContractIntegrityError,
    BusinessContractLoadError,
)
from backend.app.infrastructure.business_data.models import (
    ContractAcceptance,
    ExternalBusinessDataContract,
    ExternalBusinessDataset,
    ExternalBusinessField,
    ExternalClassification,
    ExternalRelationship,
    parse_field_reference,
)


MAX_CONTRACT_BYTES = 256 * 1024
MAX_ACCEPTANCE_BYTES = 16 * 1024
CONTRACT_RELATIVE_PATH = Path("contracts/external/business_data/v1/contract.json")
ACCEPTANCE_RELATIVE_PATH = Path("contracts/external/business_data/v1/acceptance.json")
FIELD_REFERENCE_PATTERN = re.compile(
    r"(?P<dataset>[a-z][a-z0-9_]*)\."
    r"\((?P<fields>[a-z_][a-z0-9_]*(?:\s*,\s*[a-z_][a-z0-9_]*)*)\)"
)


@dataclass(frozen=True)
class AcceptedBusinessDataContract:
    """Validated, pinned contract artifact and its exact source hash."""

    contract: ExternalBusinessDataContract
    contract_sha256: str


class BusinessDataContractCatalog:
    """Strict adapter from the accepted v1 business contract to the app catalog port."""

    def __init__(self, *, contract_path: Path, acceptance_path: Path) -> None:
        self._contract_path = Path(contract_path)
        self._acceptance_path = Path(acceptance_path)

    @classmethod
    def from_repository_root(cls, repository_root: Path) -> Self:
        """Build an adapter for the version-controlled accepted artifact."""
        return cls(
            contract_path=repository_root / CONTRACT_RELATIVE_PATH,
            acceptance_path=repository_root / ACCEPTANCE_RELATIVE_PATH,
        )

    def resolve(self, context: BusinessAnalyticsContext) -> BusinessCatalogSnapshot:
        if not isinstance(context, BusinessAnalyticsContext):
            raise BusinessAnalyticsContextError()

        accepted = self.load_accepted_contract()
        contract = accepted.contract
        if (
            context.contract_name != contract.contract_name
            or context.contract_version != contract.version
        ):
            raise BusinessCatalogMismatchError()

        allowed_scope = set(context.allowed_datasets)
        contract_scope = {dataset.name for dataset in contract.datasets}
        if not allowed_scope.issubset(contract_scope):
            raise BusinessScopeViolationError()

        scoped_datasets = tuple(
            self._map_dataset(dataset, allowed_scope)
            for dataset in contract.datasets
            if dataset.name in allowed_scope
        )
        fingerprint_input = (
            accepted.contract_sha256
            + "\n"
            + "\n".join(sorted(allowed_scope))
        ).encode("utf-8")
        catalog_fingerprint = hashlib.sha256(fingerprint_input).hexdigest()

        return BusinessCatalogSnapshot(
            tenant_id=context.tenant_id,
            contract_name=contract.contract_name,
            contract_version=contract.version,
            contract_sha256=accepted.contract_sha256,
            catalog_fingerprint=catalog_fingerprint,
            datasets=scoped_datasets,
            classification_definitions=tuple(
                BusinessClassificationDefinition(
                    classification=BusinessFieldClassification(classification.value),
                    meaning=getattr(
                        contract.data_classifications,
                        classification.value,
                    ),
                )
                for classification in ExternalClassification
            ),
        )

    def load_accepted_contract(self) -> AcceptedBusinessDataContract:
        """Load the full exact pinned artifact for consumer-side schema checks."""

        acceptance = self._load_acceptance()
        contract_bytes = self._read_bounded_file(
            self._contract_path,
            max_bytes=MAX_CONTRACT_BYTES,
        )
        actual_contract_hash = hashlib.sha256(contract_bytes).hexdigest()
        if actual_contract_hash != acceptance.contract_sha256:
            raise BusinessContractIntegrityError()
        contract = self._parse_contract(contract_bytes)
        if (
            contract.contract_name != acceptance.contract_name
            or contract.version != acceptance.accepted_version
            or contract.owner != acceptance.source_owner
        ):
            raise BusinessContractIdentityError()
        return AcceptedBusinessDataContract(
            contract=contract,
            contract_sha256=actual_contract_hash,
        )

    def _load_acceptance(self) -> ContractAcceptance:
        raw = self._read_bounded_file(
            self._acceptance_path,
            max_bytes=MAX_ACCEPTANCE_BYTES,
        )
        payload = self._parse_json(raw)
        try:
            return ContractAcceptance.model_validate(payload)
        except (ValidationError, ValueError, TypeError):
            raise BusinessContractFormatError() from None

    def _parse_contract(self, raw: bytes) -> ExternalBusinessDataContract:
        payload = self._parse_json(raw)
        try:
            return ExternalBusinessDataContract.model_validate(payload)
        except (ValidationError, ValueError, TypeError):
            raise BusinessContractFormatError() from None

    def _parse_json(self, raw: bytes) -> Any:
        if raw.startswith(b"\xef\xbb\xbf"):
            raise BusinessContractFormatError()
        try:
            text = raw.decode("utf-8")
            return json.loads(
                text,
                object_pairs_hook=self._reject_duplicate_keys,
                parse_constant=self._reject_nonstandard_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            raise BusinessContractFormatError() from None

    def _read_bounded_file(self, path: Path, *, max_bytes: int) -> bytes:
        descriptor: int | None = None
        try:
            if path.is_symlink():
                raise BusinessContractLoadError()
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                file_info = os.fstat(stream.fileno())
                if not stat.S_ISREG(file_info.st_mode) or file_info.st_size > max_bytes:
                    raise BusinessContractLoadError()
                content = stream.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise BusinessContractLoadError()
            return content
        except BusinessContractLoadError:
            raise
        except OSError:
            raise BusinessContractLoadError() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _reject_duplicate_keys(self, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for key, value in pairs:
            if key in values:
                raise ValueError("duplicate JSON object key")
            values[key] = value
        return values

    def _reject_nonstandard_constant(self, value: str) -> None:
        raise ValueError("non-standard JSON constant")

    def _map_dataset(
        self,
        dataset: ExternalBusinessDataset,
        allowed_scope: set[str],
    ) -> BusinessDataset:
        mapped_fields = tuple(
            self._map_field(field, allowed_scope) for field in dataset.fields
        )
        mapped_relationships = tuple(
            self._map_relationship(dataset, relationship)
            for relationship in dataset.relationships
            if relationship.target_dataset in allowed_scope
        )
        return BusinessDataset(
            logical_name=dataset.name,
            description=dataset.description,
            grain=dataset.grain,
            fields=mapped_fields,
            relationships=mapped_relationships,
            primary_key=BusinessDatasetPrimaryKey(
                fields=dataset.primary_key.fields,
                semantic_meaning=dataset.primary_key.semantic_meaning,
            ),
            classification=BusinessFieldClassification(dataset.sensitivity.value),
            stability=BusinessDatasetStability(
                level=dataset.stability.level,
                compatibility=dataset.stability.compatibility,
            ),
        )

    def _map_field(
        self,
        field: ExternalBusinessField,
        allowed_scope: set[str],
    ) -> BusinessField:
        reference = None
        if field.references is not None:
            target_dataset, target_fields = parse_field_reference(field.references)
            if target_dataset in allowed_scope:
                reference = BusinessFieldReference(
                    target_dataset=target_dataset,
                    target_fields=target_fields,
                )
        return BusinessField(
            name=field.name,
            logical_type=BusinessLogicalType(field.logical_type.value),
            nullable=field.nullable,
            meaning=field.semantic_meaning,
            classification=BusinessFieldClassification(field.sensitivity.value),
            description=field.description,
            enum_values=field.enum_values,
            unit=field.unit,
            serialization=field.serialization,
            currency_semantics=field.currency_semantics,
            timestamp_semantics=field.timestamp_semantics,
            identifier_semantics=field.identifier_semantics,
            references=reference,
        )

    def _map_relationship(
        self,
        source: ExternalBusinessDataset,
        relationship: ExternalRelationship,
    ) -> BusinessDatasetRelationship:
        return BusinessDatasetRelationship(
            name=relationship.field,
            target_dataset=relationship.target_dataset,
            source_fields=(source.primary_key.fields[0], relationship.field),
            target_fields=relationship.target_primary_key,
            cardinality=BusinessRelationshipCardinality(
                relationship.cardinality.value
            ),
            nullable=relationship.nullable,
            meaning=relationship.semantic_meaning,
        )
