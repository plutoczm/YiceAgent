import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.application.business_analytics.errors import (
    BusinessAnalyticsContextError,
    BusinessCatalogMismatchError,
    BusinessCatalogResolutionError,
    BusinessScopeViolationError,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessAnalyticsRequest,
    BusinessAnalyticsStatus,
    BusinessCatalogSnapshot,
    BusinessDataset,
    BusinessDatasetRelationship,
    BusinessField,
    BusinessFieldClassification,
    BusinessLogicalType,
)
from backend.app.application.business_analytics.workflow import (
    BusinessAnalyticsWorkflow,
)


def make_context(
    *,
    tenant_id: str = "tenant-1",
    contract_name: str = "business-data",
    contract_version: str = "v1",
    allowed_datasets: tuple[str, ...] = ("orders_v1", "customers_v1"),
) -> BusinessAnalyticsContext:
    return BusinessAnalyticsContext(
        request_id="request-1",
        tenant_id=tenant_id,
        contract_name=contract_name,
        contract_version=contract_version,
        allowed_datasets=allowed_datasets,
    )


def make_dataset(
    name: str = "orders_v1",
) -> BusinessDataset:
    return BusinessDataset(
        logical_name=name,
        description="Order facts at order grain",
        grain="one row per order",
        fields=(
            BusinessField(
                name="order_id",
                logical_type=BusinessLogicalType.STRING,
                nullable=False,
                meaning="Stable logical order identifier",
                classification=BusinessFieldClassification.CONFIDENTIAL,
            ),
            BusinessField(
                name="created_at",
                logical_type=BusinessLogicalType.TIMESTAMP,
                nullable=False,
                meaning="Time when the order was created",
                classification=BusinessFieldClassification.INTERNAL,
            ),
        ),
    )


def make_snapshot(
    *,
    tenant_id: str = "tenant-1",
    contract_name: str = "business-data",
    contract_version: str = "v1",
    datasets: tuple[BusinessDataset, ...] | None = None,
) -> BusinessCatalogSnapshot:
    return BusinessCatalogSnapshot(
        tenant_id=tenant_id,
        contract_name=contract_name,
        contract_version=contract_version,
        contract_sha256="a" * 64,
        catalog_fingerprint="a" * 64,
        datasets=datasets or (make_dataset(),),
    )


class FakeBusinessCatalogPort:
    def __init__(self, snapshot: BusinessCatalogSnapshot) -> None:
        self.snapshot = snapshot
        self.contexts: list[BusinessAnalyticsContext] = []

    def resolve(self, context: BusinessAnalyticsContext) -> BusinessCatalogSnapshot:
        self.contexts.append(context)
        return self.snapshot


def test_business_analytics_request_schema_exposes_only_untrusted_intent() -> None:
    schema = BusinessAnalyticsRequest.model_json_schema()

    assert set(BusinessAnalyticsRequest.model_fields) == {
        "question",
        "engine",
        "requested_datasets",
    }
    assert schema["additionalProperties"] is False
    assert "tenant_id" not in schema["properties"]
    assert "schema_context" not in schema["properties"]


@pytest.mark.parametrize(
    "untrusted_field",
    [
        "tenant_id",
        "user_id",
        "schema_context",
        "database_url",
        "username",
        "password",
        "database_password",
        "delivery_path",
        "contract_version",
        "allowed_datasets",
        "max_rows",
        "query_timeout",
        "execution_credentials",
    ],
)
def test_untrusted_request_rejects_trusted_or_execution_fields(
    untrusted_field: str,
) -> None:
    with pytest.raises(ValidationError):
        BusinessAnalyticsRequest.model_validate(
            {"question": "Summarize recent orders", untrusted_field: "attacker-value"}
        )


def test_request_scope_accepts_logical_dataset_names_only() -> None:
    request = BusinessAnalyticsRequest(
        question="Summarize orders",
        requested_datasets=("orders_v1",),
    )

    assert request.requested_datasets == ("orders_v1",)
    with pytest.raises(ValidationError):
        BusinessAnalyticsRequest(
            question="Summarize orders",
            requested_datasets=("warehouse.orders",),
        )


def test_request_rejects_duplicate_dataset_scope() -> None:
    with pytest.raises(ValidationError):
        BusinessAnalyticsRequest(
            question="Summarize orders",
            requested_datasets=("orders_v1", "orders_v1"),
        )


def test_business_analytics_models_are_immutable_and_context_is_validated() -> None:
    request = BusinessAnalyticsRequest(question="Summarize recent orders")
    context = make_context()

    with pytest.raises(ValidationError):
        request.question = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        context.tenant_id = "tenant-2"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        make_context(tenant_id=" ")
    with pytest.raises(ValidationError):
        make_context(contract_version="")
    with pytest.raises(ValidationError):
        make_context(allowed_datasets=())
    with pytest.raises(ValidationError):
        make_context(allowed_datasets=("orders_v1", "orders_v1"))

    assert "user_id" not in BusinessAnalyticsContext.model_fields


def test_trusted_port_resolves_catalog_and_prepares_requested_scope() -> None:
    context = make_context()
    port = FakeBusinessCatalogPort(make_snapshot())
    workflow = BusinessAnalyticsWorkflow(catalog_port=port)

    result = workflow.prepare(
        request=BusinessAnalyticsRequest(
            question="Summarize orders",
            requested_datasets=("orders_v1",),
        ),
        context=context,
    )

    assert result.request_id == context.request_id
    assert result.resolved_datasets == ("orders_v1",)
    assert result.status is BusinessAnalyticsStatus.PREPARED
    assert result.catalog_snapshot == port.snapshot
    assert "catalog_snapshot" not in result.model_dump()
    assert port.contexts == [context]
    assert (
        port.snapshot.datasets[0].fields[0].classification
        is BusinessFieldClassification.CONFIDENTIAL
    )


def test_catalog_models_reject_duplicate_or_unresolvable_metadata() -> None:
    dataset = make_dataset()
    with pytest.raises(ValidationError):
        make_snapshot(datasets=(dataset, dataset))

    dataset_with_missing_target = BusinessDataset(
        logical_name="orders_v1",
        description="Order facts at order grain",
        grain="one row per order",
        fields=dataset.fields,
        relationships=(
            BusinessDatasetRelationship(
                target_dataset="customers_v1",
                source_fields=("order_id",),
                target_fields=("customer_id",),
            ),
        ),
    )
    with pytest.raises(ValidationError):
        make_snapshot(datasets=(dataset_with_missing_target,))

    dataset_with_missing_field = BusinessDataset(
        logical_name="orders_v1",
        description="Order facts at order grain",
        grain="one row per order",
        fields=dataset.fields,
        relationships=(
            BusinessDatasetRelationship(
                target_dataset="orders_v1",
                source_fields=("missing_order_id",),
                target_fields=("order_id",),
            ),
        ),
    )
    with pytest.raises(ValidationError):
        make_snapshot(datasets=(dataset_with_missing_field,))


def test_unscoped_request_uses_only_datasets_returned_by_trusted_catalog() -> None:
    context = make_context()
    snapshot = make_snapshot(datasets=(make_dataset(), make_dataset("customers_v1")))
    workflow = BusinessAnalyticsWorkflow(catalog_port=FakeBusinessCatalogPort(snapshot))

    result = workflow.prepare(
        request=BusinessAnalyticsRequest(question="Summarize business activity"),
        context=context,
    )

    assert result.resolved_datasets == ("orders_v1", "customers_v1")


@pytest.mark.parametrize(
    "snapshot",
    [
        make_snapshot(tenant_id="tenant-2"),
        make_snapshot(contract_name="other-contract"),
        make_snapshot(contract_version="v2"),
    ],
)
def test_catalog_tenant_and_contract_mismatch_fail_closed(
    snapshot: BusinessCatalogSnapshot,
) -> None:
    context = make_context()
    workflow = BusinessAnalyticsWorkflow(catalog_port=FakeBusinessCatalogPort(snapshot))

    with pytest.raises(BusinessCatalogMismatchError):
        workflow.prepare(
            request=BusinessAnalyticsRequest(question="Summarize orders"),
            context=context,
        )


def test_requested_scope_outside_trusted_scope_fails_closed() -> None:
    workflow = BusinessAnalyticsWorkflow(
        catalog_port=FakeBusinessCatalogPort(make_snapshot())
    )

    with pytest.raises(BusinessScopeViolationError):
        workflow.prepare(
            request=BusinessAnalyticsRequest(
                question="Summarize payments",
                requested_datasets=("payments_v1",),
            ),
            context=make_context(),
        )


def test_requested_scope_missing_from_catalog_fails_closed() -> None:
    workflow = BusinessAnalyticsWorkflow(
        catalog_port=FakeBusinessCatalogPort(make_snapshot())
    )

    with pytest.raises(BusinessScopeViolationError):
        workflow.prepare(
            request=BusinessAnalyticsRequest(
                question="Summarize customers",
                requested_datasets=("customers_v1",),
            ),
            context=make_context(),
        )


def test_catalog_scope_wider_than_trusted_context_fails_closed() -> None:
    context = make_context(allowed_datasets=("orders_v1",))
    snapshot = make_snapshot(
        datasets=(make_dataset(), make_dataset("customers_v1"))
    )
    workflow = BusinessAnalyticsWorkflow(catalog_port=FakeBusinessCatalogPort(snapshot))

    with pytest.raises(BusinessScopeViolationError):
        workflow.prepare(
            request=BusinessAnalyticsRequest(question="Summarize orders"),
            context=context,
        )


def test_workflow_requires_context_separately_and_does_not_accept_raw_schema() -> None:
    workflow = BusinessAnalyticsWorkflow(
        catalog_port=FakeBusinessCatalogPort(make_snapshot())
    )
    request = BusinessAnalyticsRequest(question="Summarize orders")

    with pytest.raises(BusinessAnalyticsContextError):
        workflow.prepare(request=request, context=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        workflow.prepare(
            request=request,
            context=make_context(),
            schema_context="untrusted_table(secret string)",  # type: ignore[call-arg]
        )


def test_catalog_failures_use_safe_typed_errors() -> None:
    class FailingPort:
        def resolve(self, context: BusinessAnalyticsContext) -> BusinessCatalogSnapshot:
            raise RuntimeError("database_url=postgres://private")

    workflow = BusinessAnalyticsWorkflow(catalog_port=FailingPort())

    with pytest.raises(BusinessCatalogResolutionError) as error:
        workflow.prepare(
            request=BusinessAnalyticsRequest(question="Summarize orders"),
            context=make_context(),
        )

    assert error.value.code == "catalog_resolution_failed"
    assert "postgres://private" not in str(error.value)


def test_catalog_port_typed_errors_are_preserved() -> None:
    class RejectingPort:
        def resolve(self, context: BusinessAnalyticsContext) -> BusinessCatalogSnapshot:
            raise BusinessScopeViolationError()

    workflow = BusinessAnalyticsWorkflow(catalog_port=RejectingPort())

    with pytest.raises(BusinessScopeViolationError):
        workflow.prepare(
            request=BusinessAnalyticsRequest(question="Summarize orders"),
            context=make_context(),
        )


def test_catalog_port_must_return_a_typed_snapshot() -> None:
    class UntypedPort:
        def resolve(self, context: BusinessAnalyticsContext) -> BusinessCatalogSnapshot:
            return {"schema_context": "raw data"}  # type: ignore[return-value]

    workflow = BusinessAnalyticsWorkflow(catalog_port=UntypedPort())

    with pytest.raises(BusinessCatalogResolutionError):
        workflow.prepare(
            request=BusinessAnalyticsRequest(question="Summarize orders"),
            context=make_context(),
        )


def test_business_analytics_layer_does_not_depend_on_runtime_or_outer_layers() -> None:
    module_dir = (
        Path(__file__).resolve().parents[3]
        / "backend"
        / "app"
        / "application"
        / "business_analytics"
    )
    forbidden_prefixes = (
        "backend.app.presentation",
        "backend.app.infrastructure",
        "backend.app.application.agent",
        "backend.app.application.sql_review",
        "backend.app.application.text2sql.text2sql_service",
        "supportops",
        "langgraph",
        "langchain",
        "streamlit",
        "backend.app.domain.ports.llm_provider",
        "frontend",
    )

    for source_path in module_dir.glob("*.py"):
        module = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                imported_modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules = [node.module]
            else:
                continue
            assert not any(
                imported == forbidden
                or imported.startswith(f"{forbidden}.")
                for imported in imported_modules
                for forbidden in forbidden_prefixes
            ), f"{source_path.name} imports a forbidden dependency: {imported_modules}"
