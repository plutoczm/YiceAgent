import hashlib
from uuid import UUID

from backend.app.application.business_analytics.models import BusinessAnalyticsContext
from backend.app.application.identity.models import (
    AgentExecutionContext,
    AnalyticsEntrypoint,
    AuthenticatedPrincipal,
    TenantAccessGrant,
)


class BusinessAnalyticsContextFactory:
    """Build the one trusted tenant context shared by API and Agent entrypoints."""

    def create_context(
        self,
        *,
        principal: AuthenticatedPrincipal,
        grant: TenantAccessGrant,
        request_id: str,
        entrypoint: AnalyticsEntrypoint,
    ) -> BusinessAnalyticsContext:
        self._validate_grant(principal, grant)
        try:
            UUID(request_id)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("request_id must be a server-generated UUID") from None
        identity_fingerprint = hashlib.sha256(
            f"{principal.issuer}\0{principal.subject}".encode("utf-8")
        ).hexdigest()
        return BusinessAnalyticsContext(
            request_id=request_id,
            tenant_id=grant.tenant_id,
            contract_name=grant.contract_name,
            contract_version=grant.contract_version,
            allowed_datasets=grant.allowed_datasets,
            authenticated_subject_fingerprint=identity_fingerprint,
            authorization_grant_id=grant.grant_id,
            entrypoint=entrypoint,
        )

    def create_agent_context(
        self,
        *,
        principal: AuthenticatedPrincipal,
        grant: TenantAccessGrant,
        request_id: str,
    ) -> AgentExecutionContext:
        business_context = self.create_context(
            principal=principal,
            grant=grant,
            request_id=request_id,
            entrypoint="agent",
        )
        memory_user_id = hashlib.sha256(
            f"{principal.issuer}\0{principal.subject}".encode("utf-8")
        ).hexdigest()
        return AgentExecutionContext(
            request_id=request_id,
            principal=principal,
            tenant_grant=grant,
            business_analytics_context=business_context,
            memory_user_id=memory_user_id,
        )

    def _validate_grant(
        self,
        principal: AuthenticatedPrincipal,
        grant: TenantAccessGrant,
    ) -> None:
        if (grant.identity_issuer, grant.identity_subject) != (
            principal.issuer,
            principal.subject,
        ):
            raise ValueError("tenant grant does not belong to the authenticated principal")
