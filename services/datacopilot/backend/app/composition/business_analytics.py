import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.application.agent.business_analytics_adapter import (
    BusinessAnalyticsAgentAdapter,
)
from backend.app.application.business_analytics.governed_workflow import (
    GovernedBusinessAnalyticsWorkflow,
)
from backend.app.application.business_analytics.managed_models import (
    ManagedAnalyticsPolicy,
)
from backend.app.application.business_analytics.managed_text2sql import ManagedText2SQLPipeline
from backend.app.application.business_analytics.ports import ManagedSQLGeneratorPort
from backend.app.application.business_analytics.workflow import BusinessAnalyticsWorkflow
from backend.app.application.identity.context_factory import BusinessAnalyticsContextFactory
from backend.app.application.identity.errors import IdentityConfigurationError
from backend.app.application.text2sql.text2sql_service import Text2SQLService
from backend.app.core.settings import Settings
from backend.app.infrastructure.business_analytics.delivery_v2 import (
    BusinessDataDeliveryV2Consumer,
    FileBusinessDataDeliveryResolver,
)
from backend.app.infrastructure.business_analytics.duckdb_executor import (
    DuckDBBusinessAnalyticsExecutor,
)
from backend.app.infrastructure.business_analytics.logging_audit import (
    LoggingBusinessAnalyticsAuditSink,
)
from backend.app.infrastructure.business_analytics.text2sql_generator import (
    Text2SQLServiceManagedGenerator,
)
from backend.app.infrastructure.business_data.contract_catalog import (
    BusinessDataContractCatalog,
)
from backend.app.infrastructure.identity.configured_tenant_access import (
    ConfiguredTenantAccessResolver,
    TenantGrantConfiguration,
)


@dataclass(frozen=True)
class TrustedBusinessAnalyticsRuntime:
    workflow: GovernedBusinessAnalyticsWorkflow
    tenant_access_resolver: ConfiguredTenantAccessResolver
    context_factory: BusinessAnalyticsContextFactory
    agent_adapter: BusinessAnalyticsAgentAdapter


def build_trusted_business_analytics_runtime(
    settings: Settings,
    *,
    text2sql_service: Text2SQLService | None = None,
    managed_generator: ManagedSQLGeneratorPort | None = None,
    audit_sink: Any | None = None,
) -> TrustedBusinessAnalyticsRuntime:
    """Validate trusted runtime config, then compose the D1-D4 internal workflow."""
    if not settings.identity.enabled or not settings.business_analytics.enabled:
        raise IdentityConfigurationError()
    root = settings.paths.project_root.resolve()
    contract_path = _project_file(root, settings.business_analytics.contract_relative_path)
    acceptance_path = _project_file(root, settings.business_analytics.acceptance_relative_path)
    catalog = BusinessDataContractCatalog(
        contract_path=contract_path,
        acceptance_path=acceptance_path,
    )
    accepted = catalog.load_accepted_contract()
    contract = accepted.contract
    accepted_datasets = tuple(dataset.name for dataset in contract.datasets)

    grant_values = _parse_json(settings.business_analytics.tenant_grants_json, list)
    directory_values = _parse_json(
        settings.business_analytics.tenant_delivery_directories_json,
        dict,
    )
    try:
        grant_configs = tuple(
            TenantGrantConfiguration.model_validate(item)
            for item in grant_values
        )
    except (TypeError, ValueError):
        raise IdentityConfigurationError() from None
    access_resolver = ConfiguredTenantAccessResolver(
        grants=grant_configs,
        accepted_contract_name=contract.contract_name,
        accepted_contract_version=contract.version,
        accepted_dataset_names=accepted_datasets,
    )
    delivery_directories = _validate_delivery_directories(
        directory_values,
        configured_tenants={grant.tenant_id for grant in grant_configs},
    )

    delivery_resolver = FileBusinessDataDeliveryResolver(
        tenant_delivery_directories=delivery_directories,
    )
    delivery_consumer = BusinessDataDeliveryV2Consumer(
        delivery_resolver=delivery_resolver,
        contract_catalog=catalog,
    )
    managed_policy = ManagedAnalyticsPolicy()
    executor = DuckDBBusinessAnalyticsExecutor(
        delivery_consumer=delivery_consumer,
        catalog_port=catalog,
        managed_policy=managed_policy,
    )
    if managed_generator is None and text2sql_service is None:
        raise IdentityConfigurationError()
    generator = managed_generator or Text2SQLServiceManagedGenerator(
        text2sql_service=text2sql_service,
    )
    managed_pipeline = ManagedText2SQLPipeline(
        workflow=BusinessAnalyticsWorkflow(catalog_port=catalog),
        generator=generator,
        policy=managed_policy,
    )
    governed_workflow = GovernedBusinessAnalyticsWorkflow(
        text2sql_pipeline=managed_pipeline,
        execution_port=executor,
        audit_sink=audit_sink or LoggingBusinessAnalyticsAuditSink(),
    )
    return TrustedBusinessAnalyticsRuntime(
        workflow=governed_workflow,
        tenant_access_resolver=access_resolver,
        context_factory=BusinessAnalyticsContextFactory(),
        agent_adapter=BusinessAnalyticsAgentAdapter(governed_workflow),
    )


def _parse_json(raw: str, expected: type) -> Any:
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise IdentityConfigurationError() from None
    if not isinstance(value, expected):
        raise IdentityConfigurationError()
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result


def _project_file(project_root: Path, configured_path: str) -> Path:
    path = Path(configured_path)
    if path.is_absolute() or ".." in path.parts:
        raise IdentityConfigurationError()
    resolved = (project_root / path).resolve()
    if not resolved.is_relative_to(project_root) or not resolved.is_file():
        raise IdentityConfigurationError()
    return resolved


def _validate_delivery_directories(
    values: dict[str, Any],
    *,
    configured_tenants: set[str],
) -> dict[str, Path]:
    if set(values) != configured_tenants or not configured_tenants:
        raise IdentityConfigurationError()
    result: dict[str, Path] = {}
    resolved_paths: set[Path] = set()
    for tenant_id, raw_path in values.items():
        if not isinstance(raw_path, str):
            raise IdentityConfigurationError()
        path = Path(raw_path)
        if not path.is_absolute() or path.is_symlink():
            raise IdentityConfigurationError()
        try:
            exact_path = path.resolve(strict=True)
        except OSError:
            raise IdentityConfigurationError() from None
        if not exact_path.is_dir() or exact_path in resolved_paths:
            raise IdentityConfigurationError()
        result[tenant_id] = exact_path
        resolved_paths.add(exact_path)
    return result
