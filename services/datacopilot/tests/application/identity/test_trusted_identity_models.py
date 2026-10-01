from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.app.application.identity.context_factory import BusinessAnalyticsContextFactory
from backend.app.application.identity.models import (
    AgentExecutionContext,
    AuthenticatedPrincipal,
    TenantAccessGrant,
)
from backend.app.application.identity.errors import IdentityConfigurationError
from backend.app.application.identity.errors import TenantAccessDeniedError
from backend.app.infrastructure.identity.configured_tenant_access import (
    ConfiguredTenantAccessResolver,
    TenantGrantConfiguration,
)


def principal(issuer: str = "https://issuer.test/", subject: str = "sub-1"):
    return AuthenticatedPrincipal(
        issuer=issuer,
        subject=subject,
        audience=("datacopilot-api",),
        authenticated_at=datetime.now(UTC),
    )


def config(**updates):
    values = {
        "issuer": "https://issuer.test/",
        "subject": "sub-1",
        "tenant_id": "tenant-a",
        "allowed_datasets": ("orders_v1", "tickets_v1"),
        "contract_name": "supportops_business_data",
        "contract_version": "v1",
    }
    values.update(updates)
    return TenantGrantConfiguration(**values)


def resolver(*grants):
    return ConfiguredTenantAccessResolver(
        grants=grants,
        accepted_contract_name="supportops_business_data",
        accepted_contract_version="v1",
        accepted_dataset_names=("orders_v1", "tickets_v1", "actions_v1"),
    )


def test_principal_is_frozen_closed_and_has_no_credential_fields() -> None:
    identity = principal()
    assert set(AuthenticatedPrincipal.model_fields) == {
        "issuer",
        "subject",
        "audience",
        "authenticated_at",
    }
    with pytest.raises(ValidationError):
        AuthenticatedPrincipal.model_validate(
            identity.model_dump() | {"raw_token": "never-store"}
        )
    with pytest.raises(ValidationError):
        identity.subject = "changed"


def test_resolver_uses_exact_issuer_and_subject_and_returns_single_tenant() -> None:
    access = resolver(config())

    first = access.resolve_access(principal())
    same = access.resolve_access(principal())

    assert first.tenant_id == "tenant-a"
    assert first.allowed_datasets == ("orders_v1", "tickets_v1")
    assert first.grant_id == same.grant_id
    assert first.grant_id != first.identity_subject


@pytest.mark.parametrize(
    "identity",
    [principal(issuer="https://other-issuer.test/"), principal(subject="sub-2")],
)
def test_resolver_denies_unmapped_principal(identity) -> None:
    with pytest.raises(TenantAccessDeniedError):
        resolver(config()).resolve_access(identity)


@pytest.mark.parametrize(
    "invalid",
    [
        config(allowed_datasets=("missing_dataset",)),
        config(contract_version="v2"),
        config(tenant_id="../tenant-a"),
    ],
)
def test_invalid_server_grant_configuration_fails_closed(invalid) -> None:
    with pytest.raises(IdentityConfigurationError):
        resolver(invalid)


def test_duplicate_principal_configuration_fails_closed() -> None:
    with pytest.raises(IdentityConfigurationError):
        resolver(config(), config())


def test_context_factory_hashes_subject_and_keeps_user_id_separate() -> None:
    identity = principal()
    grant = resolver(config()).resolve_access(identity)
    context = BusinessAnalyticsContextFactory().create_agent_context(
        principal=identity,
        grant=grant,
        request_id="c99a8b9a-6dae-4a84-a9a8-d86ea9e5f304",
    )

    assert context.memory_user_id != identity.subject
    assert len(context.memory_user_id) == 64
    assert context.business_analytics_context.tenant_id == grant.tenant_id
    assert context.business_analytics_context.entrypoint == "agent"
    assert context.business_analytics_context.authenticated_subject_fingerprint != identity.subject


def test_context_factory_rejects_principal_grant_mismatch() -> None:
    identity = principal()
    other = resolver(config(subject="sub-2")).resolve_access(principal(subject="sub-2"))

    with pytest.raises(ValueError):
        BusinessAnalyticsContextFactory().create_context(
            principal=identity,
            grant=other,
            request_id="c99a8b9a-6dae-4a84-a9a8-d86ea9e5f304",
            entrypoint="api",
        )


def test_agent_context_constructor_cannot_combine_other_grant_or_tenant() -> None:
    identity = principal()
    grant = resolver(config()).resolve_access(identity)
    context = BusinessAnalyticsContextFactory().create_agent_context(
        principal=identity,
        grant=grant,
        request_id="c99a8b9a-6dae-4a84-a9a8-d86ea9e5f304",
    )

    with pytest.raises(ValidationError):
        AgentExecutionContext.model_validate(
            context.model_dump() | {"request_id": "another-request"}
        )


def test_identity_subject_is_not_a_tenant_even_if_values_look_similar() -> None:
    identity = principal(subject="tenant-a")
    access = resolver(config(subject="tenant-a", tenant_id="tenant-b"))

    grant: TenantAccessGrant = access.resolve_access(identity)

    assert grant.identity_subject == "tenant-a"
    assert grant.tenant_id == "tenant-b"


def test_identity_claims_reject_control_characters_empty_and_duplicate_audiences() -> None:
    with pytest.raises(ValidationError):
        principal(subject="user\nforged")
    with pytest.raises(ValidationError):
        AuthenticatedPrincipal(
            issuer="https://issuer.test/",
            subject="sub-1",
            audience=("",),
            authenticated_at=datetime.now(UTC),
        )
    with pytest.raises(ValidationError):
        AuthenticatedPrincipal(
            issuer="https://issuer.test/",
            subject="sub-1",
            audience=("api", "api"),
            authenticated_at=datetime.now(UTC),
        )


def test_grant_and_agent_context_reject_duplicate_or_mismatched_identity() -> None:
    with pytest.raises(ValidationError):
        config(allowed_datasets=("orders_v1", "orders_v1"))
    identity = principal()
    grant = resolver(config()).resolve_access(identity)
    context = BusinessAnalyticsContextFactory().create_agent_context(
        principal=identity,
        grant=grant,
        request_id="c99a8b9a-6dae-4a84-a9a8-d86ea9e5f304",
    )
    with pytest.raises(ValidationError):
        AgentExecutionContext.model_validate(
            context.model_dump() | {"principal": principal(subject="another-subject")}
        )


def test_context_factory_requires_server_generated_uuid_request_id() -> None:
    identity = principal()
    grant = resolver(config()).resolve_access(identity)

    with pytest.raises(ValueError):
        BusinessAnalyticsContextFactory().create_context(
            principal=identity,
            grant=grant,
            request_id="caller-chosen",
            entrypoint="api",
        )


def test_resolver_rejects_empty_contract_scope_and_unknown_operation() -> None:
    with pytest.raises(IdentityConfigurationError):
        ConfiguredTenantAccessResolver(
            grants=(config(),),
            accepted_contract_name="supportops_business_data",
            accepted_contract_version="v1",
            accepted_dataset_names=(),
        )
    with pytest.raises(TenantAccessDeniedError):
        resolver(config()).resolve_access(principal(), operation="business_analytics.write")
