from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    ContractName,
    ContractVersion,
    LogicalDatasetName,
    TenantIdentity,
)


class IdentityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)


class AuthenticatedPrincipal(IdentityModel):
    """Validated identity claims; credential material and raw claims are excluded."""

    issuer: str = Field(min_length=1, max_length=2048)
    subject: str = Field(min_length=1, max_length=512)
    audience: tuple[str, ...] = Field(min_length=1, max_length=16)
    authenticated_at: datetime

    @field_validator("issuer", "subject")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(character) < 0x20 for character in value):
            raise ValueError("identity values must not contain control characters")
        return value

    @field_validator("audience")
    @classmethod
    def require_unique_audiences(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not audience or any(ord(char) < 0x20 for char in audience) for audience in value):
            raise ValueError("audience entries must be non-empty printable strings")
        if len(value) != len(set(value)):
            raise ValueError("audience entries must be unique")
        return value


class TenantAccessGrant(IdentityModel):
    """A server-issued, single-tenant grant for one accepted Contract version."""

    grant_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_issuer: str = Field(min_length=1, max_length=2048)
    identity_subject: str = Field(min_length=1, max_length=512)
    tenant_id: TenantIdentity
    allowed_datasets: tuple[LogicalDatasetName, ...] = Field(min_length=1)
    contract_name: ContractName
    contract_version: ContractVersion
    authorization_source: Literal["server_configuration"] = "server_configuration"

    @field_validator("allowed_datasets")
    @classmethod
    def require_unique_datasets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("tenant grant datasets must be unique")
        return value


class AgentExecutionContext(IdentityModel):
    """Server-only Agent context. It is never serialized into AgentRequest/tool input."""

    request_id: str = Field(min_length=1, max_length=128)
    principal: AuthenticatedPrincipal
    tenant_grant: TenantAccessGrant
    business_analytics_context: BusinessAnalyticsContext
    memory_user_id: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_consistent_trusted_context(self) -> "AgentExecutionContext":
        grant = self.tenant_grant
        context = self.business_analytics_context
        if (grant.identity_issuer, grant.identity_subject) != (
            self.principal.issuer,
            self.principal.subject,
        ):
            raise ValueError("tenant grant must match the authenticated principal")
        if (
            self.request_id != context.request_id
            or grant.tenant_id != context.tenant_id
            or grant.contract_name != context.contract_name
            or grant.contract_version != context.contract_version
            or grant.allowed_datasets != context.allowed_datasets
        ):
            raise ValueError("Agent execution context must match its trusted grant")
        return self


AnalyticsEntrypoint = Literal["api", "agent", "internal"]
