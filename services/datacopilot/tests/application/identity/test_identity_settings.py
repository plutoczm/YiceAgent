import json
from pathlib import Path

import pytest

from backend.app.application.identity.errors import IdentityConfigurationError
from backend.app.composition.business_analytics import (
    build_trusted_business_analytics_runtime,
)
from backend.app.core.config import get_settings
from backend.app.core.settings import (
    BusinessAnalyticsRuntimeSettings,
    Environment,
    Settings,
    TrustedIdentitySettings,
)
from backend.app.infrastructure.business_analytics.logging_audit import (
    LoggingBusinessAnalyticsAuditSink,
)


def test_identity_is_disabled_by_default_and_does_not_trust_user_input() -> None:
    assert get_settings().identity.enabled is False
    assert get_settings().business_analytics.enabled is False


def test_enabled_identity_requires_exact_issuer_audience_and_jwks() -> None:
    with pytest.raises(ValueError):
        TrustedIdentitySettings(enabled=True)


def test_only_rs256_is_configurable_for_jwks_verification() -> None:
    with pytest.raises(ValueError):
        TrustedIdentitySettings.model_validate(
            {
                "enabled": True,
                "issuer": "https://identity.test/",
                "audience": "datacopilot-api",
                "jwks_url": "https://identity.test/jwks",
                "allowed_algorithms": ["HS256"],
            }
        )


def test_production_identity_requires_https_issuer_and_jwks() -> None:
    identity = TrustedIdentitySettings(
        enabled=True,
        issuer="http://identity.test/",
        audience="datacopilot-api",
        jwks_url="http://identity.test/jwks",
    )
    with pytest.raises(ValueError):
        Settings(
            environment=Environment.PRODUCTION,
            debug=False,
            identity=identity,
        )


def test_managed_analytics_cannot_enable_without_identity() -> None:
    with pytest.raises(ValueError):
        Settings(
            identity=TrustedIdentitySettings(enabled=False),
            business_analytics=BusinessAnalyticsRuntimeSettings(enabled=True),
        )


def configured_settings(tmp_path: Path, *, grants=None, directories=None):
    root = Path(__file__).resolve().parents[3]
    settings = get_settings().model_copy(deep=True)
    settings.identity = TrustedIdentitySettings(
        enabled=True,
        issuer="https://issuer.test/",
        audience="datacopilot-api",
        jwks_url="https://issuer.test/jwks",
    )
    grants = grants or [
        {
            "issuer": "https://issuer.test/",
            "subject": "user-a",
            "tenant_id": "tenant-a",
            "allowed_datasets": ["orders_v1"],
            "contract_name": "supportops_business_data",
            "contract_version": "v1",
        }
    ]
    directories = directories or {
        "tenant-a": str(
            root / "tests/fixtures/business_data/evaluation_v1/tenant-a"
        )
    }
    settings.business_analytics = BusinessAnalyticsRuntimeSettings(
        enabled=True,
        tenant_grants_json=json.dumps(grants),
        tenant_delivery_directories_json=json.dumps(directories),
    )
    return settings


class FakeGenerator:
    async def generate(self, request):
        raise AssertionError("configuration tests must not generate SQL")


@pytest.mark.parametrize(
    "grants, directories",
    [
        (
            [{
                "issuer": "https://issuer.test/",
                "subject": "user-a",
                "tenant_id": "tenant-a",
                "allowed_datasets": ["unknown_dataset"],
                "contract_name": "supportops_business_data",
                "contract_version": "v1",
            }],
            None,
        ),
        (
            None,
            {"tenant-a": "relative/delivery"},
        ),
        (
            None,
            {
                "tenant-a": str(
                    Path(__file__).resolve().parents[2]
                    / "tests/fixtures/business_data/evaluation_v1/tenant-a"
                ),
                "tenant-b": str(
                    Path(__file__).resolve().parents[2]
                    / "tests/fixtures/business_data/evaluation_v1/tenant-a"
                ),
            },
        ),
    ],
)
def test_runtime_rejects_invalid_scope_or_delivery_mapping(tmp_path, grants, directories) -> None:
    settings = configured_settings(tmp_path, grants=grants, directories=directories)

    with pytest.raises(IdentityConfigurationError):
        build_trusted_business_analytics_runtime(
            settings,
            managed_generator=FakeGenerator(),
        )


def test_runtime_rejects_project_contract_path_escape(tmp_path) -> None:
    settings = configured_settings(tmp_path)
    settings.business_analytics = BusinessAnalyticsRuntimeSettings(
        enabled=True,
        tenant_grants_json=settings.business_analytics.tenant_grants_json,
        tenant_delivery_directories_json=(
            settings.business_analytics.tenant_delivery_directories_json
        ),
        contract_relative_path="../outside.json",
    )

    with pytest.raises(IdentityConfigurationError):
        build_trusted_business_analytics_runtime(
            settings,
            managed_generator=FakeGenerator(),
        )


def test_duplicate_keys_in_trusted_grant_json_are_rejected(tmp_path) -> None:
    settings = configured_settings(tmp_path)
    settings.business_analytics = BusinessAnalyticsRuntimeSettings(
        enabled=True,
        tenant_grants_json='[{"issuer":"https://issuer.test/","issuer":"https://evil.test/"}]',
        tenant_delivery_directories_json=(
            settings.business_analytics.tenant_delivery_directories_json
        ),
    )

    with pytest.raises(IdentityConfigurationError):
        build_trusted_business_analytics_runtime(
            settings,
            managed_generator=FakeGenerator(),
        )


def test_runtime_requires_both_identity_and_managed_analytics(tmp_path) -> None:
    settings = get_settings().model_copy(deep=True)

    with pytest.raises(IdentityConfigurationError):
        build_trusted_business_analytics_runtime(
            settings,
            managed_generator=FakeGenerator(),
        )


def test_runtime_requires_a_generator(tmp_path) -> None:
    settings = configured_settings(tmp_path)

    with pytest.raises(IdentityConfigurationError):
        build_trusted_business_analytics_runtime(settings)


def test_runtime_defaults_to_structured_logging_audit_sink(tmp_path) -> None:
    runtime = build_trusted_business_analytics_runtime(
        configured_settings(tmp_path),
        managed_generator=FakeGenerator(),
    )

    assert isinstance(runtime.workflow._audit_sink, LoggingBusinessAnalyticsAuditSink)


@pytest.mark.parametrize(
    "grants, directories",
    [
        ([{"issuer": "https://issuer.test/"}], None),
        ("{}", None),
        (None, {"tenant-a": 42}),
        (None, {"tenant-a": str(Path("C:/not-present/datacopilot-delivery"))}),
        (None, {"unexpected-tenant": str(Path("C:/not-present/datacopilot-delivery"))}),
    ],
)
def test_runtime_rejects_malformed_grants_and_delivery_maps(
    tmp_path,
    grants,
    directories,
) -> None:
    settings = configured_settings(tmp_path, grants=grants, directories=directories)

    with pytest.raises(IdentityConfigurationError):
        build_trusted_business_analytics_runtime(
            settings,
            managed_generator=FakeGenerator(),
        )


def test_runtime_rejects_missing_contract_file(tmp_path) -> None:
    settings = configured_settings(tmp_path)
    settings.business_analytics = BusinessAnalyticsRuntimeSettings(
        enabled=True,
        tenant_grants_json=settings.business_analytics.tenant_grants_json,
        tenant_delivery_directories_json=(
            settings.business_analytics.tenant_delivery_directories_json
        ),
        contract_relative_path="contracts/not-present.json",
    )

    with pytest.raises(IdentityConfigurationError):
        build_trusted_business_analytics_runtime(
            settings,
            managed_generator=FakeGenerator(),
        )


def test_runtime_rejects_symlink_directory_path_by_predicate(tmp_path, monkeypatch) -> None:
    settings = configured_settings(tmp_path)
    root = Path(__file__).resolve().parents[3]
    delivery = root / "tests/fixtures/business_data/evaluation_v1/tenant-a"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == delivery or original(path))

    with pytest.raises(IdentityConfigurationError):
        build_trusted_business_analytics_runtime(
            settings,
            managed_generator=FakeGenerator(),
        )
