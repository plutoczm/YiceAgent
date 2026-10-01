import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.application.business_analytics.errors import (
    BusinessSQLParseError,
    BusinessSQLAuthorizationError,
    BusinessSQLPolicyViolationError,
    BusinessSchemaRenderError,
)
from backend.app.application.business_analytics.managed_models import (
    EffectiveBusinessCatalog,
    ManagedAnalyticsPolicy,
    ManagedBusinessSQLStatus,
    ManagedGenericValidation,
    ManagedGenericValidationIssue,
    ManagedSQLDraft,
    ManagedSQLGenerationRequest,
    ManagedSQLReferencedField,
    ManagedTokenUsage,
)
from backend.app.application.business_analytics.managed_policy import (
    build_effective_catalog,
)
from backend.app.application.business_analytics.managed_text2sql import (
    ManagedText2SQLPipeline,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessAnalyticsRequest,
    BusinessAnalyticsStatus,
    BusinessCatalogSnapshot,
    BusinessDataset,
    BusinessFieldClassification,
)
from backend.app.application.business_analytics.ports import BusinessCatalogPort
from backend.app.application.business_analytics.schema_renderer import (
    BusinessSchemaRenderer,
)
from backend.app.application.business_analytics.sql_authorization import (
    DIALECT_BY_ENGINE,
    ManagedSQLAuthorizer,
)
from backend.app.application.business_analytics.workflow import (
    BusinessAnalyticsWorkflow,
)
from backend.app.application.text2sql.models import (
    SQLEngine,
    SQLValidationIssue,
    SQLValidationResult,
)
from backend.app.application.text2sql.schema_service import SchemaService
from backend.app.infrastructure.business_data.contract_catalog import (
    BusinessDataContractCatalog,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def make_context(
    *,
    tenant_id: str = "tenant-secret-value",
    request_id: str = "request-secret-value",
    allowed_datasets: tuple[str, ...] = ("orders_v1",),
) -> BusinessAnalyticsContext:
    return BusinessAnalyticsContext(
        request_id=request_id,
        tenant_id=tenant_id,
        contract_name="supportops_business_data",
        contract_version="v1",
        allowed_datasets=allowed_datasets,
    )


def make_effective_catalog(
    *,
    engine: SQLEngine = SQLEngine.HIVE,
    allowed_datasets: tuple[str, ...] = ("orders_v1",),
) -> EffectiveBusinessCatalog:
    context = make_context(allowed_datasets=allowed_datasets)
    port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    prepared = BusinessAnalyticsWorkflow(catalog_port=port).prepare(
        request=BusinessAnalyticsRequest(
            question="Summarize the authorized business data",
            requested_datasets=allowed_datasets,
            engine=engine,
        ),
        context=context,
    )
    return build_effective_catalog(
        prepared,
        engine=engine,
        policy=ManagedAnalyticsPolicy(),
    )


class FakeCatalogPort:
    def __init__(self, snapshot: BusinessCatalogSnapshot) -> None:
        self.snapshot = snapshot
        self.contexts: list[BusinessAnalyticsContext] = []

    def resolve(self, context: BusinessAnalyticsContext) -> BusinessCatalogSnapshot:
        self.contexts.append(context)
        return self.snapshot


class FakeManagedSQLGenerator:
    def __init__(
        self,
        sql: str,
        *,
        validation: ManagedGenericValidation | None = None,
    ) -> None:
        self.sql = sql
        self.validation = validation or ManagedGenericValidation(is_valid=True)
        self.requests: list[ManagedSQLGenerationRequest] = []

    async def generate(self, request: ManagedSQLGenerationRequest) -> ManagedSQLDraft:
        self.requests.append(request)
        return ManagedSQLDraft(
            candidate_sql=self.sql,
            generic_validation=self.validation,
            token_usage=ManagedTokenUsage(
                prompt_tokens=21,
                completion_tokens=13,
                total_tokens=34,
            ),
            model_explanation="Model explanation is not validation evidence.",
        )


def make_real_workflow(context: BusinessAnalyticsContext):
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    return catalog_port, ManagedText2SQLPipeline(
        workflow=__import__(
            "backend.app.application.business_analytics.workflow",
            fromlist=["BusinessAnalyticsWorkflow"],
        ).BusinessAnalyticsWorkflow(catalog_port=catalog_port),
        generator=FakeManagedSQLGenerator(
            "SELECT status, COUNT(*) AS total FROM orders_v1 GROUP BY status"
        ),
    )


def test_managed_generator_request_has_no_context_or_policy_fields() -> None:
    schema = ManagedSQLGenerationRequest.model_json_schema()

    assert set(ManagedSQLGenerationRequest.model_fields) == {
        "question",
        "engine",
        "rendered_schema",
    }
    assert schema["additionalProperties"] is False
    for forbidden in (
        "tenant_id",
        "contract_version",
        "schema_context",
        "database_name",
        "credentials",
        "allowed_datasets",
        "allowed_classifications",
        "max_rows",
        "query_timeout",
        "use_rag",
    ):
        assert forbidden not in schema["properties"]
        with pytest.raises(ValidationError):
            ManagedSQLGenerationRequest.model_validate(
                {
                    "question": "Summarize orders",
                    "engine": "hive",
                    "rendered_schema": "orders_v1(status STRING)",
                    forbidden: "untrusted override",
                }
            )


def test_schema_renderer_is_stable_scoped_and_metadata_preserving() -> None:
    context = make_context(allowed_datasets=("tickets_v1", "orders_v1"))
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    from backend.app.application.business_analytics.workflow import BusinessAnalyticsWorkflow

    workflow = BusinessAnalyticsWorkflow(catalog_port=catalog_port)
    request = BusinessAnalyticsRequest(question="Count statuses")
    prepared = workflow.prepare(request=request, context=context)
    effective = build_effective_catalog(
        prepared,
        engine=SQLEngine.HIVE,
        policy=ManagedAnalyticsPolicy(),
    )
    renderer = BusinessSchemaRenderer()
    rendered = renderer.render(effective, max_chars=32_768)

    other_context = context.model_copy(
        update={"request_id": "another-request", "tenant_id": "tenant-b"}
    )
    other_prepared = workflow.prepare(request=request, context=other_context)
    other_effective = build_effective_catalog(
        other_prepared,
        engine=SQLEngine.HIVE,
        policy=ManagedAnalyticsPolicy(),
    )

    assert rendered == renderer.render(other_effective, max_chars=32_768)
    assert "orders_v1" in rendered and "tickets_v1" in rendered
    assert "actions_v1" not in rendered
    assert context.tenant_id not in rendered
    assert context.request_id not in rendered
    assert "grain" in rendered
    assert "primary_key" in rendered
    assert "enum_values" in rendered
    assert "currency_semantics" in rendered
    assert str(REPOSITORY_ROOT) not in rendered
    metadata = json.loads(
        renderer.build_validator_schema(effective)
        .require_table("orders_v1")
        .description
    )
    assert metadata["primary_key"] is not None

    parsed_schema = SchemaService().parse_schema_context(rendered)
    assert parsed_schema.table_names == {"orders_v1", "tickets_v1"}
    assert parsed_schema.require_table("orders_v1").require_column("amount")
    assert parsed_schema.require_table("tickets_v1").description is not None
    assert "primary_key" in parsed_schema.require_table("orders_v1").description


def test_schema_renderer_respects_char_bound_and_engine_type_hints() -> None:
    context = make_context()
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    from backend.app.application.business_analytics.workflow import BusinessAnalyticsWorkflow

    prepared = BusinessAnalyticsWorkflow(catalog_port=catalog_port).prepare(
        request=BusinessAnalyticsRequest(question="Count orders"),
        context=context,
    )
    renderer = BusinessSchemaRenderer()

    with pytest.raises(BusinessSchemaRenderError):
        renderer.render(
            build_effective_catalog(
                prepared,
                engine=SQLEngine.HIVE,
                policy=ManagedAnalyticsPolicy(),
            ),
            max_chars=32,
        )

    for engine, hint in (
        (SQLEngine.HIVE, "DECIMAL"),
        (SQLEngine.SPARK_SQL, "DECIMAL"),
        (SQLEngine.MYSQL, "DECIMAL"),
        (SQLEngine.CLICKHOUSE, "Decimal"),
    ):
        effective = build_effective_catalog(
            prepared,
            engine=engine,
            policy=ManagedAnalyticsPolicy(),
        )
        assert hint in renderer.render(effective, max_chars=32_768)
        assert DIALECT_BY_ENGINE[engine] in {"hive", "spark", "mysql", "clickhouse"}


def test_restricted_fields_are_absent_from_effective_view_and_renderer() -> None:
    context = make_context()
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    snapshot = catalog_port.resolve(context)
    orders = snapshot.datasets[0]
    restricted_fields = tuple(
        field.model_copy(update={"classification": BusinessFieldClassification.RESTRICTED})
        if field.name == "amount"
        else field
        for field in orders.fields
    )
    modified_orders = orders.model_copy(update={"fields": restricted_fields})
    restricted_snapshot = snapshot.model_copy(update={"datasets": (modified_orders,)})
    workflow = __import__(
        "backend.app.application.business_analytics.workflow",
        fromlist=["BusinessAnalyticsWorkflow"],
    ).BusinessAnalyticsWorkflow(catalog_port=FakeCatalogPort(restricted_snapshot))
    prepared = workflow.prepare(
        request=BusinessAnalyticsRequest(question="Sum amount"),
        context=context,
    )
    effective = build_effective_catalog(
        prepared,
        engine=SQLEngine.HIVE,
        policy=ManagedAnalyticsPolicy(),
    )
    rendered = BusinessSchemaRenderer().render(effective, max_chars=32_768)

    assert "amount" not in {field.name for field in effective.datasets[0].fields}
    assert '"name":"amount"' not in rendered


@pytest.mark.anyio
async def test_pipeline_returns_ready_scoped_plan_with_metadata_only_tenant_scope() -> None:
    context = make_context()
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    from backend.app.application.business_analytics.workflow import BusinessAnalyticsWorkflow

    generator = FakeManagedSQLGenerator(
        "SELECT status, COUNT(*) AS total FROM orders_v1 GROUP BY status"
    )
    pipeline = ManagedText2SQLPipeline(
        workflow=BusinessAnalyticsWorkflow(catalog_port=catalog_port),
        generator=generator,
    )

    plan = await pipeline.generate(
        request=BusinessAnalyticsRequest(
            question="Count orders by status",
            requested_datasets=("orders_v1",),
            engine=SQLEngine.HIVE,
        ),
        context=context,
    )

    assert plan.status is ManagedBusinessSQLStatus.READY
    assert plan.tenant_id == context.tenant_id
    assert plan.effective_datasets == ("orders_v1",)
    assert plan.referenced_datasets == ("orders_v1",)
    assert ManagedSQLReferencedField(
        dataset_name="orders_v1",
        field_name="status",
    ) in plan.referenced_fields
    assert plan.generated_sql.endswith("LIMIT 100")
    assert "tenant-secret-value" not in plan.generated_sql
    assert "tenant-secret-value" not in generator.requests[0].rendered_schema
    assert "tickets_v1" not in generator.requests[0].rendered_schema
    assert plan.max_rows == 100
    assert plan.token_usage.total_tokens == 34
    assert plan.validation.generic_safety_valid is True
    assert plan.validation.parse_valid is True
    assert plan.model_explanation == "Model explanation is not validation evidence."
    assert plan.status is not ManagedBusinessSQLStatus.READY or not hasattr(plan, "executed_rows")


@pytest.mark.anyio
async def test_pipeline_uses_same_effective_catalog_for_rendering_and_ast_authorization() -> None:
    context = make_context(
        allowed_datasets=("tickets_v1", "ticket_events_v1"),
    )
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    from backend.app.application.business_analytics.workflow import BusinessAnalyticsWorkflow

    generator = FakeManagedSQLGenerator(
        "SELECT t.status, COUNT(*) AS event_count "
        "FROM tickets_v1 t JOIN ticket_events_v1 e "
        "ON t.tenant_id = e.tenant_id AND t.ticket_id = e.ticket_id "
        "GROUP BY t.status"
    )
    pipeline = ManagedText2SQLPipeline(
        workflow=BusinessAnalyticsWorkflow(catalog_port=catalog_port),
        generator=generator,
    )

    plan = await pipeline.generate(
        request=BusinessAnalyticsRequest(
            question="Count transitions by ticket status",
            requested_datasets=("ticket_events_v1", "tickets_v1"),
            engine=SQLEngine.HIVE,
        ),
        context=context,
    )

    assert plan.effective_datasets == ("ticket_events_v1", "tickets_v1")
    assert plan.referenced_datasets == ("ticket_events_v1", "tickets_v1")
    assert plan.catalog_fingerprint == catalog_port.resolve(context).catalog_fingerprint
    assert "orders_v1" not in generator.requests[0].rendered_schema


@pytest.mark.anyio
async def test_pipeline_applies_managed_row_limit_after_generic_500_row_limit() -> None:
    context = make_context()
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    from backend.app.application.business_analytics.workflow import BusinessAnalyticsWorkflow

    generator = FakeManagedSQLGenerator(
        "SELECT status FROM orders_v1 LIMIT 500"
    )
    pipeline = ManagedText2SQLPipeline(
        workflow=BusinessAnalyticsWorkflow(catalog_port=catalog_port),
        generator=generator,
        policy=ManagedAnalyticsPolicy(max_rows=100),
    )

    plan = await pipeline.generate(
        request=BusinessAnalyticsRequest(
            question="List order statuses",
            requested_datasets=("orders_v1",),
        ),
        context=context,
    )

    assert plan.generated_sql.endswith("LIMIT 100")
    assert plan.max_rows == 100


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("engine", "sql", "expected"),
    [
        (SQLEngine.HIVE, "SELECT status FROM orders_v1 LIMIT 50", "LIMIT 50"),
        (
            SQLEngine.MYSQL,
            "SELECT status FROM orders_v1 LIMIT 10, 500",
            "LIMIT 10, 100",
        ),
    ],
)
async def test_pipeline_preserves_lower_limit_and_caps_mysql_offset_count(
    engine: SQLEngine,
    sql: str,
    expected: str,
) -> None:
    port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    pipeline = ManagedText2SQLPipeline(
        workflow=BusinessAnalyticsWorkflow(catalog_port=port),
        generator=FakeManagedSQLGenerator(sql),
        policy=ManagedAnalyticsPolicy(max_rows=100),
    )

    plan = await pipeline.generate(
        request=BusinessAnalyticsRequest(
            question="List order statuses",
            requested_datasets=("orders_v1",),
            engine=engine,
        ),
        context=make_context(),
    )

    assert plan.generated_sql.endswith(expected)
    assert plan.max_rows == 100


@pytest.mark.anyio
async def test_pipeline_rejects_comment_candidate_and_sql_over_size_policy() -> None:
    port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    workflow = BusinessAnalyticsWorkflow(catalog_port=port)
    request = BusinessAnalyticsRequest(
        question="List order statuses",
        requested_datasets=("orders_v1",),
    )
    context = make_context()

    comments = ManagedText2SQLPipeline(
        workflow=workflow,
        generator=FakeManagedSQLGenerator(
            "SELECT status FROM orders_v1 -- trailing comment"
        ),
    )
    with pytest.raises(BusinessSQLPolicyViolationError):
        await comments.generate(request=request, context=context)

    too_long = ManagedText2SQLPipeline(
        workflow=workflow,
        generator=FakeManagedSQLGenerator("SELECT 1 " + " " * 300),
        policy=ManagedAnalyticsPolicy(max_sql_chars=256),
    )
    with pytest.raises(BusinessSQLPolicyViolationError):
        await too_long.generate(request=request, context=context)


def test_authorizer_uses_engine_dialects_and_rejects_unknown_datasets() -> None:
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    authorizer = ManagedSQLAuthorizer()
    for engine, dialect in (
        (SQLEngine.HIVE, "hive"),
        (SQLEngine.SPARK_SQL, "spark"),
        (SQLEngine.MYSQL, "mysql"),
        (SQLEngine.CLICKHOUSE, "clickhouse"),
    ):
        prepared = BusinessAnalyticsWorkflow(catalog_port=catalog_port).prepare(
            request=BusinessAnalyticsRequest(
                question="Count authorized orders",
                requested_datasets=("orders_v1",),
                engine=engine,
            ),
            context=make_context(),
        )
        effective = build_effective_catalog(
            prepared,
            engine=engine,
            policy=ManagedAnalyticsPolicy(),
        )
        assert DIALECT_BY_ENGINE[engine] == dialect
        evidence = authorizer.authorize(
            sql="SELECT status FROM orders_v1 LIMIT 25",
            catalog=effective,
            generic_validation=ManagedGenericValidation(is_valid=True),
            max_rows=100,
        )
        assert evidence.referenced_datasets == ("orders_v1",)

    with pytest.raises(BusinessSQLAuthorizationError):
        authorizer.authorize(
            sql="SELECT status FROM tickets_v1 LIMIT 25",
            catalog=make_effective_catalog(),
            generic_validation=ManagedGenericValidation(is_valid=True),
        )


def test_renderer_removes_relationships_outside_the_effective_scope() -> None:
    renderer = BusinessSchemaRenderer()
    only_events = make_effective_catalog(
        allowed_datasets=("ticket_events_v1",),
    )
    parsed = SchemaService().parse_schema_context(
        renderer.render(only_events, max_chars=32_768)
    )
    assert parsed.require_table("ticket_events_v1")
    metadata = json.loads(
        renderer.build_validator_schema(only_events)
        .require_table("ticket_events_v1")
        .description
    )

    assert metadata["relationships"] == []
    assert all(field.references is None for field in only_events.datasets[0].fields)

    with_both = make_effective_catalog(
        allowed_datasets=("ticket_events_v1", "tickets_v1"),
    )
    both_metadata = json.loads(
        renderer.build_validator_schema(with_both)
        .require_table("ticket_events_v1")
        .description
    )
    effective_names = {dataset.logical_name for dataset in with_both.datasets}
    assert both_metadata["relationships"]
    assert all(
        relation["target_dataset"] in effective_names
        for relation in both_metadata["relationships"]
    )


@pytest.mark.parametrize(
    "sql",
    [
        "WITH base AS (SELECT status FROM orders_v1), "
        "nested AS (SELECT status FROM base) "
        "SELECT status FROM nested LIMIT 100",
        "SELECT q.status FROM (SELECT status FROM orders_v1) q LIMIT 100",
        "SELECT status FROM orders_v1 UNION ALL "
        "SELECT refund_status FROM orders_v1 LIMIT 100",
        "SELECT tickets_v1.status FROM orders_v1 AS tickets_v1 LIMIT 100",
    ],
)
def test_authorizer_resolves_cte_subquery_union_and_alias_scopes(sql: str) -> None:
    evidence = ManagedSQLAuthorizer().authorize(
        sql=sql,
        catalog=make_effective_catalog(),
        generic_validation=ManagedGenericValidation(is_valid=True),
        max_rows=100,
    )
    assert evidence.referenced_datasets == ("orders_v1",)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH base AS (SELECT status FROM tickets_v1) "
        "SELECT status FROM base LIMIT 100",
        "SELECT q.status FROM (SELECT status FROM tickets_v1) q LIMIT 100",
        "SELECT status FROM orders_v1 UNION ALL "
        "SELECT status FROM tickets_v1 LIMIT 100",
        "SELECT mystery_fn(status) FROM orders_v1 LIMIT 100",
    ],
)
def test_authorizer_rejects_nested_unauthorized_sources_and_unknown_functions(
    sql: str,
) -> None:
    with pytest.raises(BusinessSQLAuthorizationError):
        ManagedSQLAuthorizer().authorize(
            sql=sql,
            catalog=make_effective_catalog(),
            generic_validation=ManagedGenericValidation(is_valid=True),
        )


@pytest.mark.anyio
async def test_pipeline_keeps_cte_alias_false_positive_under_ast_scope_control() -> None:
    context = make_context()
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    from backend.app.application.business_analytics.workflow import BusinessAnalyticsWorkflow

    cte_sql = (
        "WITH by_status AS (SELECT status FROM orders_v1) "
        "SELECT status FROM by_status"
    )
    generator = FakeManagedSQLGenerator(
        cte_sql,
        validation=ManagedGenericValidation(
            is_valid=False,
            issues=(
                ManagedGenericValidationIssue(
                    code="unknown_table",
                    severity="error",
                    object_name="by_status",
                ),
            ),
        ),
    )
    pipeline = ManagedText2SQLPipeline(
        workflow=BusinessAnalyticsWorkflow(catalog_port=catalog_port),
        generator=generator,
    )

    plan = await pipeline.generate(
        request=BusinessAnalyticsRequest(
            question="Count statuses using a CTE",
            requested_datasets=("orders_v1",),
        ),
        context=context,
    )

    assert plan.status is ManagedBusinessSQLStatus.READY
    assert plan.referenced_datasets == ("orders_v1",)


@pytest.mark.anyio
async def test_pipeline_fails_closed_for_unknown_or_disallowed_sql_fields() -> None:
    context = make_context()
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    from backend.app.application.business_analytics.workflow import BusinessAnalyticsWorkflow

    for sql in (
        "SELECT status FROM users",
        "SELECT credit_card_number FROM orders_v1",
        "SELECT order_id FROM prod.orders_v1",
        "SELECT * FROM orders_v1",
        "SELECT o.* FROM orders_v1 o",
        "INSERT INTO orders_v1 SELECT * FROM orders_v1",
        "SELECT status FROM orders_v1; DELETE FROM orders_v1",
        "SELECT status FROM read_csv('orders.csv')",
    ):
        pipeline = ManagedText2SQLPipeline(
            workflow=BusinessAnalyticsWorkflow(catalog_port=catalog_port),
            generator=FakeManagedSQLGenerator(sql),
        )
        with pytest.raises(
            (
                BusinessSQLAuthorizationError,
                BusinessSQLParseError,
                BusinessSQLPolicyViolationError,
            )
        ):
            await pipeline.generate(
                request=BusinessAnalyticsRequest(
                    question="Analyze orders",
                    requested_datasets=("orders_v1",),
                ),
                context=context,
            )


@pytest.mark.anyio
async def test_pipeline_rejects_ambiguous_unqualified_column() -> None:
    context = make_context(allowed_datasets=("orders_v1", "tickets_v1"))
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    from backend.app.application.business_analytics.workflow import BusinessAnalyticsWorkflow

    pipeline = ManagedText2SQLPipeline(
        workflow=BusinessAnalyticsWorkflow(catalog_port=catalog_port),
        generator=FakeManagedSQLGenerator(
            "SELECT tenant_id FROM orders_v1 o "
            "JOIN tickets_v1 t ON o.tenant_id = t.tenant_id"
        ),
    )

    with pytest.raises(BusinessSQLAuthorizationError):
        await pipeline.generate(
            request=BusinessAnalyticsRequest(
                question="Count tenants",
                requested_datasets=("orders_v1", "tickets_v1"),
            ),
            context=context,
        )


@pytest.mark.anyio
async def test_pipeline_applies_classification_policy_before_generator() -> None:
    context = make_context()
    source = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT).resolve(
        context
    )
    orders = source.datasets[0]
    restricted_fields = tuple(
        field.model_copy(update={"classification": BusinessFieldClassification.RESTRICTED})
        if field.name == "amount"
        else field
        for field in orders.fields
    )
    restricted_orders = orders.model_copy(update={"fields": restricted_fields})
    snapshot = source.model_copy(update={"datasets": (restricted_orders,)})
    from backend.app.application.business_analytics.workflow import BusinessAnalyticsWorkflow

    generator = FakeManagedSQLGenerator("SELECT amount FROM orders_v1")
    pipeline = ManagedText2SQLPipeline(
        workflow=BusinessAnalyticsWorkflow(catalog_port=FakeCatalogPort(snapshot)),
        generator=generator,
    )

    with pytest.raises(BusinessSQLAuthorizationError):
        await pipeline.generate(
            request=BusinessAnalyticsRequest(
                question="Sum order amounts",
                requested_datasets=("orders_v1",),
            ),
            context=context,
        )
    assert '"name":"amount"' not in generator.requests[0].rendered_schema


@pytest.mark.anyio
async def test_pipeline_fails_closed_when_policy_removes_all_fields_before_generation() -> None:
    context = make_context()
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    from backend.app.application.business_analytics.workflow import BusinessAnalyticsWorkflow

    generator = FakeManagedSQLGenerator("SELECT 1")
    pipeline = ManagedText2SQLPipeline(
        workflow=BusinessAnalyticsWorkflow(catalog_port=catalog_port),
        generator=generator,
        policy=ManagedAnalyticsPolicy(
            allowed_classifications=(BusinessFieldClassification.PUBLIC,),
        ),
    )

    with pytest.raises(BusinessSQLPolicyViolationError):
        await pipeline.generate(
            request=BusinessAnalyticsRequest(question="Count orders"),
            context=context,
        )
    assert generator.requests == []


def test_generator_request_cannot_control_scope_or_policy() -> None:
    schema = ManagedSQLGenerationRequest.model_json_schema()

    assert set(ManagedSQLGenerationRequest.model_fields) == {
        "question",
        "engine",
        "rendered_schema",
    }
    assert schema["additionalProperties"] is False
    for field in (
        "tenant_id",
        "schema_context",
        "database_name",
        "allowed_datasets",
        "allowed_classifications",
        "max_rows",
        "query_timeout",
        "credentials",
    ):
        with pytest.raises(ValidationError):
            ManagedSQLGenerationRequest.model_validate(
                {
                    "question": "Summarize orders",
                    "engine": "hive",
                    "rendered_schema": "orders_v1(status STRING)",
                    field: "override",
                }
            )
