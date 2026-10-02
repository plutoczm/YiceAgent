from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
import pytest

from backend.app.application.identity.errors import (
    AuthenticationFailedError,
    IdentityConfigurationError,
    IdentityProviderUnavailableError,
)
from backend.app.application.identity.models import AuthenticatedPrincipal
from backend.app.core.config import get_settings
from backend.app.core.settings import (
    BusinessAnalyticsRuntimeSettings,
    TrustedIdentitySettings,
)
from backend.app.presentation.api.dependencies import business_analytics as dependencies


def configured_settings(*, enabled: bool = True):
    settings = get_settings().model_copy(deep=True)
    settings.identity = TrustedIdentitySettings(
        enabled=enabled,
        issuer="https://identity.test/",
        audience="datacopilot-api",
        jwks_url="https://identity.test/jwks",
    )
    settings.business_analytics = BusinessAnalyticsRuntimeSettings(enabled=enabled)
    return settings


def credentials(scheme: str = "Bearer") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme=scheme, credentials="opaque")


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        issuer="https://identity.test/",
        subject="user-1",
        audience=("datacopilot-api",),
        authenticated_at=datetime.now(UTC),
    )


class ReturnPrincipal:
    def authenticate_bearer(self, token):
        return principal()


@pytest.mark.parametrize(
    ("provider", "expected_status", "expected_code"),
    [
        (None, 401, "authentication_required"),
        ("invalid-scheme", 401, "authentication_required"),
        ("missing-provider", 503, "identity_provider_unavailable"),
        (AuthenticationFailedError(), 401, "invalid_bearer_token"),
        (IdentityProviderUnavailableError(), 503, "identity_provider_unavailable"),
        (IdentityConfigurationError(), 503, "identity_provider_unavailable"),
        (RuntimeError("secret transport detail"), 503, "identity_provider_unavailable"),
    ],
)
def test_bearer_dependency_has_stable_fail_closed_outcomes(
    provider,
    expected_status,
    expected_code,
) -> None:
    if provider == "invalid-scheme":
        with pytest.raises(HTTPException) as caught:
            dependencies.get_authenticated_principal(
                credentials=credentials("Basic"),
                provider=ReturnPrincipal(),
            )
    elif provider == "missing-provider":
        with pytest.raises(HTTPException) as caught:
            dependencies.get_authenticated_principal(
                credentials=credentials(),
                provider=None,
            )
    elif isinstance(provider, Exception):
        class BrokenProvider:
            def authenticate_bearer(self, token):
                raise provider

        with pytest.raises(HTTPException) as caught:
            dependencies.get_authenticated_principal(
                credentials=credentials(),
                provider=BrokenProvider(),
            )
    else:
        with pytest.raises(HTTPException) as caught:
            dependencies.get_authenticated_principal(
                credentials=None,
                provider=provider,
            )
    assert caught.value.status_code == expected_status
    assert caught.value.detail["code"] == expected_code
    assert "secret transport detail" not in str(caught.value.detail)


def test_valid_bearer_dependency_returns_verified_principal() -> None:
    result = dependencies.get_authenticated_principal(
        credentials=credentials(),
        provider=ReturnPrincipal(),
    )
    assert result.subject == "user-1"


def test_identity_provider_is_disabled_by_default(monkeypatch) -> None:
    settings = configured_settings(enabled=False)
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(dependencies, "_identity_provider", None)
    monkeypatch.setattr(dependencies, "_identity_provider_key", None)

    assert dependencies.get_trusted_identity_provider() is None


def test_configured_identity_provider_is_created_once_and_cached(monkeypatch) -> None:
    settings = configured_settings()
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(dependencies, "_identity_provider", None)
    monkeypatch.setattr(dependencies, "_identity_provider_key", None)

    first = dependencies.get_trusted_identity_provider()
    second = dependencies.get_trusted_identity_provider()

    assert first is second
    assert first._issuer == "https://identity.test/"


def test_enabled_but_incomplete_provider_configuration_returns_503(monkeypatch) -> None:
    settings = SimpleNamespace(
        identity=SimpleNamespace(
            enabled=True,
            issuer=None,
            audience=None,
            jwks_url=None,
        )
    )
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)

    with pytest.raises(HTTPException) as caught:
        dependencies.get_trusted_identity_provider()
    assert caught.value.status_code == 503


def test_unconfigured_runtime_returns_503(monkeypatch) -> None:
    settings = configured_settings(enabled=False)
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(dependencies, "_analytics_runtime", None)

    with pytest.raises(HTTPException) as caught:
        dependencies.get_trusted_analytics_runtime()
    assert caught.value.status_code == 503


def test_configured_runtime_is_cached_and_builder_failure_is_hidden(monkeypatch) -> None:
    settings = configured_settings()
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(dependencies, "_analytics_runtime", None)
    calls = []
    sentinel = object()
    monkeypatch.setattr(dependencies, "get_text2sql_service", lambda: "service")
    monkeypatch.setattr(
        dependencies,
        "build_trusted_business_analytics_runtime",
        lambda config, *, text2sql_service: calls.append(text2sql_service) or sentinel,
    )

    assert dependencies.get_trusted_analytics_runtime() is sentinel
    assert dependencies.get_trusted_analytics_runtime() is sentinel
    assert calls == ["service"]

    monkeypatch.setattr(dependencies, "_analytics_runtime", None)
    monkeypatch.setattr(
        dependencies,
        "build_trusted_business_analytics_runtime",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("private config")),
    )
    with pytest.raises(HTTPException) as caught:
        dependencies.get_trusted_analytics_runtime()
    assert caught.value.status_code == 503
    assert "private config" not in str(caught.value.detail)


def test_authorized_context_dependencies_create_server_contexts(monkeypatch) -> None:
    settings = configured_settings()
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    from tests.api.test_business_analytics_api import make_runtime

    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        runtime = make_runtime(Path(temp))
        request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
        identity = AuthenticatedPrincipal(
            issuer="https://issuer.test/",
            subject="user-a",
            audience=("datacopilot-api",),
            authenticated_at=datetime.now(UTC),
        )
        api_call = dependencies.get_authorized_api_analytics_call(
            request,
            principal=identity,
            runtime=runtime,
        )
        assert api_call.business_context.entrypoint == "api"
        assert request.state.business_analytics_request_id == api_call.business_context.request_id
        request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
        agent_context = dependencies.get_authorized_agent_execution_context(
            request,
            principal=identity,
            runtime=runtime,
        )
        assert agent_context.business_analytics_context.entrypoint == "agent"
        assert agent_context.request_id == request.state.business_analytics_request_id


def test_authorized_context_dependencies_deny_and_fail_closed_on_factory_errors() -> None:
    identity = principal()

    class DeniedResolver:
        def resolve_access(self, principal, *, operation="business_analytics.read"):
            from backend.app.application.identity.errors import TenantAccessDeniedError

            raise TenantAccessDeniedError()

    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    with pytest.raises(HTTPException) as denied:
        dependencies.get_authorized_api_analytics_call(
            request,
            principal=identity,
            runtime=SimpleNamespace(tenant_access_resolver=DeniedResolver()),
        )
    assert denied.value.status_code == 403

    class BadFactory:
        def create_context(self, **kwargs):
            raise ValueError("private tenant configuration")

        def create_agent_context(self, **kwargs):
            raise ValueError("private tenant configuration")

    grant = SimpleNamespace()

    class Resolver:
        def resolve_access(self, principal, *, operation="business_analytics.read"):
            return grant

    runtime = SimpleNamespace(tenant_access_resolver=Resolver(), context_factory=BadFactory())
    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    with pytest.raises(HTTPException) as api_error:
        dependencies.get_authorized_api_analytics_call(
            request,
            principal=identity,
            runtime=runtime,
        )
    assert api_error.value.status_code == 503
    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    with pytest.raises(HTTPException) as agent_error:
        dependencies.get_authorized_agent_execution_context(
            request,
            principal=identity,
            runtime=runtime,
        )
    assert agent_error.value.status_code == 503
