from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.tenancy import TenantId

_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}\Z")


class ExportValidationError(ValueError):
    """Raised when an export context or contract-shaped record is invalid."""


class DeliveryValidationError(ValueError):
    """Raised when an untrusted Business Data delivery fails closed validation."""


class ContractCompatibilityError(ValueError):
    """Raised when a v1 contract breaks its frozen compatibility surface."""


@dataclass(frozen=True)
class DeliveryContext:
    """Trusted delivery scope, not caller-supplied business data."""

    tenant_id: str
    currency: str

    def __post_init__(self) -> None:
        try:
            tenant_id = str(TenantId(self.tenant_id))
        except ValueError as exc:
            raise ExportValidationError("invalid_export_tenant_id") from exc
        if not isinstance(self.currency, str) or not _CURRENCY_PATTERN.fullmatch(self.currency):
            raise ExportValidationError("invalid_export_currency")

        object.__setattr__(self, "tenant_id", tenant_id)


@dataclass(frozen=True)
class ExportResult:
    output_path: Path
    manifest: dict[str, object]


@dataclass(frozen=True)
class DeliveryValidationResult:
    valid: bool
    contract_version: str
    export_format_version: str
    tenant_id: str
    currency: str
    dataset_row_counts: dict[str, int]
    snapshot: dict[str, object]
    delivery_fingerprint: str
