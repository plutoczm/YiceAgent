from typing import Protocol

from pydantic import SecretStr

from backend.app.application.identity.models import (
    AuthenticatedPrincipal,
    TenantAccessGrant,
)


class TrustedIdentityProviderPort(Protocol):
    """Verify caller-supplied bearer credentials against trusted issuer config."""

    def authenticate_bearer(self, token: SecretStr) -> AuthenticatedPrincipal: ...


class TenantAccessResolverPort(Protocol):
    """Resolve a server-owned tenant grant for an authenticated subject."""

    def resolve_access(
        self,
        principal: AuthenticatedPrincipal,
        *,
        operation: str = "business_analytics.read",
    ) -> TenantAccessGrant: ...
