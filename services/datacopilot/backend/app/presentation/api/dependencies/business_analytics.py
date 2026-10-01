from dataclasses import dataclass
from uuid import uuid4

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr

from backend.app.application.identity.errors import (
    AuthenticationFailedError,
    IdentityConfigurationError,
    IdentityProviderUnavailableError,
    TenantAccessDeniedError,
)
from backend.app.application.identity.models import (
    AgentExecutionContext,
    AuthenticatedPrincipal,
    TenantAccessGrant,
)
from backend.app.application.business_analytics.models import BusinessAnalyticsContext
from backend.app.core.config import get_settings
from backend.app.domain.ports.trusted_identity import TrustedIdentityProviderPort
from backend.app.composition.business_analytics import (
    TrustedBusinessAnalyticsRuntime,
    build_trusted_business_analytics_runtime,
)
from backend.app.infrastructure.identity.pyjwt_jwks_provider import (
    PyJWTJwksIdentityProvider,
)
from backend.app.presentation.api.dependencies.providers import get_text2sql_service


bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth")
_identity_provider: TrustedIdentityProviderPort | None = None
_identity_provider_key: tuple[str, str, str, int, int, int, float] | None = None
_analytics_runtime: TrustedBusinessAnalyticsRuntime | None = None


@dataclass(frozen=True)
class AuthorizedAnalyticsCall:
    principal: AuthenticatedPrincipal
    grant: TenantAccessGrant
    business_context: BusinessAnalyticsContext
    runtime: TrustedBusinessAnalyticsRuntime


def get_trusted_identity_provider() -> TrustedIdentityProviderPort | None:
    global _identity_provider, _identity_provider_key
    settings = get_settings().identity
    if not settings.enabled:
        return None
    if not (settings.issuer and settings.audience and settings.jwks_url):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "identity_provider_unavailable",
                "message": "Business analytics identity verification is not configured",
            },
        )
    key = (
        str(settings.issuer),
        settings.audience,
        str(settings.jwks_url),
        settings.clock_skew_seconds,
        settings.max_bearer_token_bytes,
        settings.jwks_cache_seconds,
        settings.jwks_timeout_seconds,
    )
    if _identity_provider is None or _identity_provider_key != key:
        _identity_provider = PyJWTJwksIdentityProvider(
            issuer=key[0],
            audience=key[1],
            jwks_url=key[2],
            clock_skew_seconds=key[3],
            max_token_bytes=key[4],
            jwks_cache_seconds=key[5],
            jwks_timeout_seconds=key[6],
            allowed_algorithms=settings.allowed_algorithms,
            require_https=get_settings().environment.value == "production",
        )
        _identity_provider_key = key
    return _identity_provider


def get_trusted_analytics_runtime() -> TrustedBusinessAnalyticsRuntime:
    global _analytics_runtime
    settings = get_settings()
    if not settings.identity.enabled or not settings.business_analytics.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "business_analytics_unavailable",
                "message": "Trusted business analytics is not configured",
            },
        )
    if _analytics_runtime is None:
        try:
            _analytics_runtime = build_trusted_business_analytics_runtime(
                settings,
                text2sql_service=get_text2sql_service(),
            )
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "business_analytics_unavailable",
                    "message": "Trusted business analytics is not configured",
                },
            ) from None
    return _analytics_runtime


def get_authenticated_principal(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    provider: TrustedIdentityProviderPort | None = Depends(get_trusted_identity_provider),
) -> AuthenticatedPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "authentication_required", "message": "Bearer authentication required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "identity_provider_unavailable",
                "message": "Business analytics identity verification is not configured",
            },
        )
    try:
        return provider.authenticate_bearer(SecretStr(credentials.credentials))
    except AuthenticationFailedError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_bearer_token", "message": "Bearer token is invalid"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except IdentityProviderUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "identity_provider_unavailable",
                "message": "Identity verification is temporarily unavailable",
            },
        ) from None
    except IdentityConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "identity_provider_unavailable",
                "message": "Business analytics identity verification is not configured",
            },
        ) from None
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "identity_provider_unavailable",
                "message": "Identity verification is temporarily unavailable",
            },
        ) from None


def get_authorized_api_analytics_call(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    runtime: TrustedBusinessAnalyticsRuntime = Depends(get_trusted_analytics_runtime),
) -> AuthorizedAnalyticsCall:
    request_id = str(uuid4())
    try:
        grant = runtime.tenant_access_resolver.resolve_access(principal)
    except TenantAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "business_analytics_access_denied", "message": "No analytics grant"},
        ) from None
    try:
        context = runtime.context_factory.create_context(
            principal=principal,
            grant=grant,
            request_id=request_id,
            entrypoint="api",
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "business_analytics_unavailable",
                "message": "Trusted analytics context is unavailable",
            },
        ) from None
    request.state.business_analytics_request_id = request_id
    return AuthorizedAnalyticsCall(
        principal=principal,
        grant=grant,
        business_context=context,
        runtime=runtime,
    )


def get_authorized_agent_execution_context(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    runtime: TrustedBusinessAnalyticsRuntime = Depends(get_trusted_analytics_runtime),
) -> AgentExecutionContext:
    request_id = str(uuid4())
    try:
        grant = runtime.tenant_access_resolver.resolve_access(principal)
    except TenantAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "business_analytics_access_denied", "message": "No analytics grant"},
        ) from None
    try:
        context = runtime.context_factory.create_agent_context(
            principal=principal,
            grant=grant,
            request_id=request_id,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "business_analytics_unavailable",
                "message": "Trusted Agent execution context is unavailable",
            },
        ) from None
    request.state.business_analytics_request_id = request_id
    return context
