import hashlib
import json
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.application.identity.errors import (
    IdentityConfigurationError,
    TenantAccessDeniedError,
)
from backend.app.application.identity.models import (
    AuthenticatedPrincipal,
    TenantAccessGrant,
)
from backend.app.domain.ports.trusted_identity import TenantAccessResolverPort


class TenantGrantConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    issuer: str = Field(min_length=1, max_length=2048)
    subject: str = Field(min_length=1, max_length=512)
    tenant_id: str = Field(min_length=1, max_length=128)
    allowed_datasets: tuple[str, ...] = Field(min_length=1)
    contract_name: str = Field(min_length=1, max_length=64)
    contract_version: str = Field(min_length=1, max_length=64)

    @field_validator("allowed_datasets")
    @classmethod
    def require_unique_datasets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("configured tenant grant datasets must be unique")
        return value


class ConfiguredTenantAccessResolver(TenantAccessResolverPort):
    """Resolve exact issuer/subject grants from trusted server configuration."""

    def __init__(
        self,
        *,
        grants: Iterable[TenantGrantConfiguration],
        accepted_contract_name: str,
        accepted_contract_version: str,
        accepted_dataset_names: Iterable[str],
    ) -> None:
        accepted = frozenset(accepted_dataset_names)
        if not accepted:
            raise IdentityConfigurationError()
        mapping: dict[tuple[str, str], TenantGrantConfiguration] = {}
        for grant in grants:
            key = (grant.issuer, grant.subject)
            if key in mapping:
                raise IdentityConfigurationError()
            if (
                grant.contract_name != accepted_contract_name
                or grant.contract_version != accepted_contract_version
                or not set(grant.allowed_datasets).issubset(accepted)
            ):
                raise IdentityConfigurationError()
            try:
                TenantAccessGrant(
                    grant_id="0" * 64,
                    identity_issuer=grant.issuer,
                    identity_subject=grant.subject,
                    tenant_id=grant.tenant_id,
                    allowed_datasets=grant.allowed_datasets,
                    contract_name=grant.contract_name,
                    contract_version=grant.contract_version,
                )
            except ValueError:
                raise IdentityConfigurationError() from None
            mapping[key] = grant
        self._grants = mapping

    def resolve_access(
        self,
        principal: AuthenticatedPrincipal,
        *,
        operation: str = "business_analytics.read",
    ) -> TenantAccessGrant:
        if operation != "business_analytics.read":
            raise TenantAccessDeniedError()
        configured = self._grants.get((principal.issuer, principal.subject))
        if configured is None:
            raise TenantAccessDeniedError()
        material = {
            "allowed_datasets": sorted(configured.allowed_datasets),
            "contract_name": configured.contract_name,
            "contract_version": configured.contract_version,
            "issuer": configured.issuer,
            "subject": configured.subject,
            "tenant_id": configured.tenant_id,
        }
        grant_id = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return TenantAccessGrant(
            grant_id=grant_id,
            identity_issuer=configured.issuer,
            identity_subject=configured.subject,
            tenant_id=configured.tenant_id,
            allowed_datasets=configured.allowed_datasets,
            contract_name=configured.contract_name,
            contract_version=configured.contract_version,
        )
