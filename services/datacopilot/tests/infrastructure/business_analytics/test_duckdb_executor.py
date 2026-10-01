from __future__ import annotations

import asyncio
import decimal
import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import duckdb
import pytest
from pydantic import ValidationError

from backend.app.application.business_analytics.errors import (
    BusinessDataDeliveryError,
    BusinessExecutionTimeoutError,
    BusinessResultLimitError,
    BusinessSQLAuthorizationError,
    BusinessSQLPolicyViolationError,
    BusinessSQLTranslationError,
)
from backend.app.application.business_analytics.governed_models import (
    BusinessAnalyticsAuditEvent,
    BusinessAnalyticsAuditStatus,
    GovernedExecutionPolicy,
    GovernedResultColumn,
)
from backend.app.application.business_analytics.governed_workflow import (
    GovernedBusinessAnalyticsWorkflow,
)
from backend.app.application.business_analytics.managed_models import (
    ManagedAnalyticsPolicy,
    ManagedGenericValidation,
    ManagedSQLDraft,
    ManagedSQLGenerationRequest,
    ManagedTokenUsage,
)
from backend.app.application.business_analytics.managed_policy import (
    build_effective_catalog,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessAnalyticsRequest,
    BusinessFieldClassification,
)
from backend.app.application.business_analytics.workflow import (
    BusinessAnalyticsWorkflow,
)
from backend.app.application.text2sql.models import SQLEngine
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
from backend.app.infrastructure.business_data.contract_catalog import (
    BusinessDataContractCatalog,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPOSITORY_ROOT / "tests" / "fixtures" / "business_data" / "delivery_v2"
ALL_DATASETS = (
    "orders_v1",
    "tickets_v1",
    "ticket_events_v1",
    "actions_v1",
)


class FakeManagedSQLGenerator:
    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.requests: list[ManagedSQLGenerationRequest] = []

    async def generate(self, request: ManagedSQLGenerationRequest) -> ManagedSQLDraft:
        self.requests.append(request)
        return ManagedSQLDraft(
            candidate_sql=self.sql,
            generic_validation=ManagedGenericValidation(is_valid=True),
            token_usage=ManagedTokenUsage(
                prompt_tokens=3,
                completion_tokens=2,
                total_tokens=5,
            ),
            model_explanation="Synthetic candidate explanation",
        )


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[BusinessAnalyticsAuditEvent] = []

    def emit(self, event: BusinessAnalyticsAuditEvent) -> None:
        self.events.append(event)


def make_context(
    allowed_datasets: tuple[str, ...] = ("orders_v1",),
) -> BusinessAnalyticsContext:
    return BusinessAnalyticsContext(
        request_id="request-synthetic-001",
        tenant_id="tenant-synthetic",
        contract_name="supportops_business_data",
        contract_version="v1",
        allowed_datasets=allowed_datasets,
    )


def make_effective_catalog_for_context(context: BusinessAnalyticsContext):
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    request = BusinessAnalyticsRequest(
        question="List authorized data",
        requested_datasets=context.allowed_datasets,
        engine=SQLEngine.HIVE,
    )
    prepared = BusinessAnalyticsWorkflow(catalog_port=catalog_port).prepare(
        request=request,
        context=context,
    )
    return build_effective_catalog(
        prepared,
        engine=request.engine,
        policy=ManagedAnalyticsPolicy(),
    )


def make_stack(
    *,
    sql: str,
    delivery_root: Path = FIXTURE,
    engine: SQLEngine = SQLEngine.HIVE,
    d3_policy: ManagedAnalyticsPolicy | None = None,
    d4_policy: GovernedExecutionPolicy | None = None,
    audit: InMemoryAuditSink | None = None,
):
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    generator = FakeManagedSQLGenerator(sql)
    managed_policy = d3_policy or ManagedAnalyticsPolicy()
    pipeline = __import__(
        "backend.app.application.business_analytics.managed_text2sql",
        fromlist=["ManagedText2SQLPipeline"],
    ).ManagedText2SQLPipeline(
        workflow=BusinessAnalyticsWorkflow(catalog_port=catalog_port),
        generator=generator,
        policy=managed_policy,
    )
    consumer = BusinessDataDeliveryV2Consumer(
        delivery_resolver=FileBusinessDataDeliveryResolver(
            tenant_delivery_directories={"tenant-synthetic": delivery_root}
        ),
        contract_catalog=catalog_port,
    )
    executor = DuckDBBusinessAnalyticsExecutor(
        delivery_consumer=consumer,
        catalog_port=catalog_port,
        managed_policy=managed_policy,
    )
    audit_sink = audit or InMemoryAuditSink()
    workflow = GovernedBusinessAnalyticsWorkflow(
        text2sql_pipeline=pipeline,
        execution_port=executor,
        audit_sink=audit_sink,
        policy=d4_policy or GovernedExecutionPolicy(),
    )
    return workflow, pipeline, executor, audit_sink, generator


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def canonical_json_line(value: dict[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def write_orders(delivery: Path, amounts: tuple[str, ...]) -> None:
    source = json.loads((delivery / "orders_v1.jsonl").read_text(encoding="utf-8"))
    records = []
    for index, amount in enumerate(amounts):
        record = dict(source)
        record["order_id"] = f"order-synthetic-{index:03d}"
        record["customer_id"] = f"customer-synthetic-{index:03d}"
        record["status"] = "paid" if index % 2 == 0 else "pending"
        record["amount"] = amount
        record["refundable_amount"] = amount
        records.append(record)
    content = b"".join(canonical_json_line(record) for record in records)
    (delivery / "orders_v1.jsonl").write_bytes(content)
    manifest_path = delivery / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor = next(
        item for item in manifest["datasets"] if item["dataset_name"] == "orders_v1"
    )
    descriptor["row_count"] = len(records)
    descriptor["content_sha256"] = hashlib.sha256(content).hexdigest()
    material = {
        "contract_sha256": manifest["schema_sha256"],
        "contract_version": manifest["contract_version"],
        "currency": manifest["currency"],
        "datasets": [
            {
                "content_sha256": item["content_sha256"],
                "dataset_name": item["dataset_name"],
                "row_count": item["row_count"],
            }
            for item in manifest["datasets"]
        ],
        "tenant_id": manifest["tenant_id"],
    }
    manifest["snapshot"]["snapshot_id"] = hashlib.sha256(
        canonical_json(material)
    ).hexdigest()
    manifest_path.write_bytes(canonical_json(manifest))


@pytest.mark.anyio
async def test_full_internal_workflow_executes_and_audits_bounded_result() -> None:
    context = make_context()
    audit = InMemoryAuditSink()
    workflow, _, _, _, _ = make_stack(
        sql=(
            "SELECT status, COUNT(*) AS total FROM orders_v1 "
            "GROUP BY status ORDER BY status"
        ),
        audit=audit,
    )

    result = await workflow.run(
        request=BusinessAnalyticsRequest(
            question="Count synthetic orders by status",
            requested_datasets=("orders_v1",),
        ),
        context=context,
    )

    assert result.status.value == "executed"
    assert result.rows == (("paid", 1),)
    assert result.row_count == 1
    assert result.columns[0].name == "status"
    assert result.snapshot.currency == "USD"
    assert result.snapshot.effective_datasets == ("orders_v1",)
    assert result.snapshot.tenant_id == context.tenant_id
    assert result.result_classification is BusinessFieldClassification.INTERNAL
    assert result.source_plan_reauthorized is True
    assert result.translated_sql_reauthorized is True
    assert len(result.query_fingerprint) == 64
    assert [event.status for event in audit.events] == [
        BusinessAnalyticsAuditStatus.STARTED,
        BusinessAnalyticsAuditStatus.EXECUTED,
    ]
    audit_json = " ".join(event.model_dump_json() for event in audit.events)
    assert "paid" not in audit_json
    assert "SELECT" not in audit_json
    assert str(FIXTURE) not in audit_json
    result_json = result.model_dump_json()
    assert str(FIXTURE) not in result_json
    assert "generated_sql" not in result_json


@pytest.mark.anyio
async def test_count_star_uses_dataset_classification_fallback() -> None:
    workflow, _, _, _, _ = make_stack(
        sql="SELECT COUNT(*) AS total FROM orders_v1"
    )
    result = await workflow.run(
        request=BusinessAnalyticsRequest(
            question="Count the authorized dataset",
            requested_datasets=("orders_v1",),
        ),
        context=make_context(),
    )
    assert result.referenced_fields == ()
    assert result.result_classification is BusinessFieldClassification.CONFIDENTIAL


@pytest.mark.anyio
async def test_decimal_values_are_materialized_and_returned_without_float_rounding(
    tmp_path: Path,
) -> None:
    delivery = tmp_path / "delivery"
    import shutil

    shutil.copytree(FIXTURE, delivery)
    amounts = (
        "0",
        "1",
        "1.20",
        "0.0001",
        "1234567890123456789012345678901234",
    )
    write_orders(delivery, amounts)
    workflow, _, _, _, _ = make_stack(
        sql="SELECT order_id, amount FROM orders_v1 ORDER BY order_id",
        delivery_root=delivery,
    )

    result = await workflow.run(
        request=BusinessAnalyticsRequest(
            question="List exact synthetic order values",
            requested_datasets=("orders_v1",),
        ),
        context=make_context(),
    )

    assert result.columns[1].data_type == "DECIMAL(38,4)"
    assert [decimal.Decimal(value) for _, value in result.rows] == [
        decimal.Decimal(value) for value in amounts
    ]
    assert all(isinstance(value, str) for _, value in result.rows)


def test_materializer_creates_only_effective_datasets_and_fields() -> None:
    from backend.app.application.business_analytics.models import (
        BusinessAnalyticsResult,
    )
    from backend.app.application.business_analytics.managed_policy import (
        build_effective_catalog,
    )
    from backend.app.infrastructure.business_analytics.delivery_v2 import (
        BusinessDataDeliveryV2Consumer,
        FileBusinessDataDeliveryResolver,
    )

    context = make_context(allowed_datasets=ALL_DATASETS)
    contract_catalog = BusinessDataContractCatalog.from_repository_root(
        REPOSITORY_ROOT
    )
    snapshot = contract_catalog.resolve(context)
    d3_policy = ManagedAnalyticsPolicy(
        allowed_classifications=(BusinessFieldClassification.INTERNAL,),
    )
    prepared = BusinessAnalyticsResult(
        request_id=context.request_id,
        contract_name=snapshot.contract_name,
        contract_version=snapshot.contract_version,
        catalog_fingerprint=snapshot.catalog_fingerprint,
        resolved_datasets=("orders_v1",),
        catalog_snapshot=snapshot,
    )
    effective = build_effective_catalog(
        prepared,
        engine=SQLEngine.HIVE,
        policy=d3_policy,
    )
    consumer = BusinessDataDeliveryV2Consumer(
        delivery_resolver=FileBusinessDataDeliveryResolver(
            tenant_delivery_directories={"tenant-synthetic": FIXTURE}
        ),
        contract_catalog=contract_catalog,
    )
    governed_policy = GovernedExecutionPolicy()
    delivery = consumer.validate(
        context=context,
        catalog=effective,
        policy=governed_policy,
    )
    executor = DuckDBBusinessAnalyticsExecutor(
        delivery_consumer=consumer,
        catalog_port=contract_catalog,
        managed_policy=d3_policy,
    )
    connection = executor._open_sandbox(governed_policy)
    try:
        executor._materializer.materialize(
            connection,
            delivery,
            effective,
            governed_policy,
        )
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
            ).fetchall()
        }
        assert table_names == {"orders_v1"}
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('orders_v1')").fetchall()
        }
        assert columns == {
            "status",
            "amount",
            "refundable_amount",
            "refund_status",
            "return_status",
        }
        assert "tenant_id" not in columns
        assert "customer_id" not in columns
        assert "currency" not in columns
        with pytest.raises(duckdb.CatalogException):
            connection.execute("SELECT * FROM tickets_v1")
    finally:
        connection.close()


def test_restricted_contract_field_is_never_materialized() -> None:
    from backend.app.application.business_analytics.models import (
        BusinessAnalyticsResult,
        BusinessCatalogSnapshot,
    )
    from backend.app.application.business_analytics.managed_policy import (
        build_effective_catalog,
    )
    from backend.app.infrastructure.business_analytics.delivery_v2 import (
        BusinessDataDeliveryV2Consumer,
        FileBusinessDataDeliveryResolver,
    )

    context = make_context()
    contract_catalog = BusinessDataContractCatalog.from_repository_root(
        REPOSITORY_ROOT
    )
    source = contract_catalog.resolve(context)
    orders = source.datasets[0]
    changed_orders = orders.model_copy(
        update={
            "fields": tuple(
                field.model_copy(
                    update={"classification": BusinessFieldClassification.RESTRICTED}
                )
                if field.name == "amount"
                else field
                for field in orders.fields
            )
        }
    )
    restricted_snapshot = source.model_copy(
        update={"datasets": (changed_orders,)}
    )

    class RestrictedCatalogPort:
        def resolve(self, _context):
            return restricted_snapshot

    prepared = BusinessAnalyticsWorkflow(catalog_port=RestrictedCatalogPort()).prepare(
        request=BusinessAnalyticsRequest(
            question="Count orders",
            requested_datasets=("orders_v1",),
        ),
        context=context,
    )
    policy = ManagedAnalyticsPolicy()
    effective = build_effective_catalog(
        prepared,
        engine=SQLEngine.HIVE,
        policy=policy,
    )
    assert "amount" not in {field.name for field in effective.datasets[0].fields}

    consumer = BusinessDataDeliveryV2Consumer(
        delivery_resolver=FileBusinessDataDeliveryResolver(
            tenant_delivery_directories={"tenant-synthetic": FIXTURE}
        ),
        contract_catalog=contract_catalog,
    )
    execution_policy = GovernedExecutionPolicy()
    delivery = consumer.validate(
        context=context,
        catalog=effective,
        policy=execution_policy,
    )
    executor = DuckDBBusinessAnalyticsExecutor(
        delivery_consumer=consumer,
        catalog_port=contract_catalog,
        managed_policy=policy,
    )
    connection = executor._open_sandbox(execution_policy)
    try:
        executor._materializer.materialize(
            connection,
            delivery,
            effective,
            execution_policy,
        )
        materialized_fields = {
            row[1]
            for row in connection.execute("PRAGMA table_info('orders_v1')").fetchall()
        }
        assert "amount" not in materialized_fields
    finally:
        connection.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("engine", "sql", "dataset_scope"),
    [
        (
            SQLEngine.HIVE,
            "SELECT status FROM orders_v1 LIMIT 10",
            ("orders_v1",),
        ),
        (
            SQLEngine.SPARK_SQL,
            "SELECT status FROM orders_v1 LIMIT 10",
            ("orders_v1",),
        ),
        (
            SQLEngine.MYSQL,
            "SELECT status FROM orders_v1 LIMIT 10",
            ("orders_v1",),
        ),
        (
            SQLEngine.CLICKHOUSE,
            "SELECT status FROM orders_v1 LIMIT 10",
            ("orders_v1",),
        ),
    ],
)
async def test_four_source_dialects_translate_reauthorize_and_execute(
    engine: SQLEngine,
    sql: str,
    dataset_scope: tuple[str, ...],
) -> None:
    workflow, _, _, _, _ = make_stack(sql=sql, engine=engine)
    result = await workflow.run(
        request=BusinessAnalyticsRequest(
            question="List synthetic order status",
            requested_datasets=dataset_scope,
            engine=engine,
        ),
        context=make_context(dataset_scope),
    )
    assert result.status.value == "executed"
    assert result.execution_engine == "duckdb"
    assert result.rows == (("paid",),)
    assert result.source_plan_reauthorized is True
    assert result.translated_sql_reauthorized is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sql", "datasets", "expected_rows"),
    [
        (
            "SELECT SUM(amount) AS total_amount FROM orders_v1",
            ("orders_v1",),
            (("12.30",),),
        ),
        (
            "SELECT priority, COUNT(*) AS n FROM tickets_v1 GROUP BY priority",
            ("tickets_v1",),
            (("high", 1),),
        ),
        (
            "SELECT t.status, COUNT(*) AS n FROM tickets_v1 t "
            "JOIN ticket_events_v1 e ON t.tenant_id = e.tenant_id "
            "AND t.ticket_id = e.ticket_id GROUP BY t.status",
            ("tickets_v1", "ticket_events_v1"),
            (("open", 1),),
        ),
        (
            "SELECT action_status, COUNT(*) AS n FROM actions_v1 "
            "GROUP BY action_status",
            ("actions_v1",),
            (("submitted", 1),),
        ),
        (
            "WITH counts AS (SELECT status, COUNT(*) AS n FROM orders_v1 GROUP BY status) "
            "SELECT status, n FROM counts",
            ("orders_v1",),
            (("paid", 1),),
        ),
        (
            "SELECT q.status FROM (SELECT status FROM orders_v1) q",
            ("orders_v1",),
            (("paid",),),
        ),
        (
            "SELECT status FROM orders_v1 UNION ALL SELECT status FROM orders_v1",
            ("orders_v1",),
            (("paid",), ("paid",)),
        ),
    ],
)
async def test_supported_execution_queries(
    sql: str,
    datasets: tuple[str, ...],
    expected_rows: tuple[tuple[object, ...], ...],
) -> None:
    workflow, _, _, _, _ = make_stack(sql=sql)
    result = await workflow.run(
        request=BusinessAnalyticsRequest(
            question="Run a synthetic governed analytics query",
            requested_datasets=datasets,
        ),
        context=make_context(datasets),
    )
    assert result.rows == expected_rows


@pytest.mark.anyio
async def test_query_fingerprint_is_stable_for_same_authorized_query() -> None:
    context = make_context()
    request = BusinessAnalyticsRequest(
        question="List order status",
        requested_datasets=("orders_v1",),
    )
    first, *_ = make_stack(sql="SELECT status FROM orders_v1")
    second, *_ = make_stack(sql="SELECT status FROM orders_v1")

    first_result = await first.run(request=request, context=context)
    second_result = await second.run(request=request, context=context)

    assert first_result.query_fingerprint == second_result.query_fingerprint
    assert first_result.snapshot.snapshot_id == second_result.snapshot.snapshot_id


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sql", "dataset_scope", "engine", "expected_rows"),
    [
        (
            "SELECT `status` FROM `orders_v1` LIMIT 10",
            ("orders_v1",),
            SQLEngine.HIVE,
            (("paid",),),
        ),
        (
            "SELECT `status` FROM `orders_v1` LIMIT 10",
            ("orders_v1",),
            SQLEngine.SPARK_SQL,
            (("paid",),),
        ),
        (
            "SELECT `status` FROM `orders_v1` LIMIT 0, 1",
            ("orders_v1",),
            SQLEngine.MYSQL,
            (("paid",),),
        ),
        (
            "SELECT `status` FROM `orders_v1` LIMIT 10",
            ("orders_v1",),
            SQLEngine.CLICKHOUSE,
            (("paid",),),
        ),
    ],
)
async def test_quoted_identifiers_and_dialect_limits_keep_semantics(
    sql: str,
    dataset_scope: tuple[str, ...],
    engine: SQLEngine,
    expected_rows: tuple[tuple[object, ...], ...],
) -> None:
    workflow, _, _, _, _ = make_stack(sql=sql, engine=engine)
    result = await workflow.run(
        request=BusinessAnalyticsRequest(
            question="Read one authorized status",
            requested_datasets=dataset_scope,
            engine=engine,
        ),
        context=make_context(dataset_scope),
    )
    assert result.rows == expected_rows


@pytest.mark.anyio
async def test_timestamp_boolean_nullable_and_currency_metadata_semantics() -> None:
    timestamp_workflow, _, _, _, _ = make_stack(
        sql="SELECT created_at FROM tickets_v1"
    )
    timestamp_result = await timestamp_workflow.run(
        request=BusinessAnalyticsRequest(
            question="Read ticket creation time",
            requested_datasets=("tickets_v1",),
        ),
        context=make_context(("tickets_v1",)),
    )
    assert timestamp_result.columns[0].data_type == "TIMESTAMP"
    assert timestamp_result.rows == (("2026-09-24T00:00:00Z",),)

    nullable_workflow, _, _, _, _ = make_stack(
        sql="SELECT assignee_id FROM tickets_v1"
    )
    nullable_result = await nullable_workflow.run(
        request=BusinessAnalyticsRequest(
            question="Read nullable assignee",
            requested_datasets=("tickets_v1",),
        ),
        context=make_context(("tickets_v1",)),
    )
    assert nullable_result.rows == ((None,),)

    boolean_workflow, _, _, _, _ = make_stack(
        sql="SELECT approval_required FROM actions_v1"
    )
    boolean_result = await boolean_workflow.run(
        request=BusinessAnalyticsRequest(
            question="Read action approval flag",
            requested_datasets=("actions_v1",),
        ),
        context=make_context(("actions_v1",)),
    )
    assert boolean_result.rows == ((True,),)
    assert boolean_result.snapshot.currency == "USD"
    assert "currency" not in {
        field.name
        for dataset in make_effective_catalog_for_context(
            make_context(("orders_v1",))
        ).datasets
        for field in dataset.fields
    }

    comparison_workflow, _, _, _, _ = make_stack(
        sql=(
            "SELECT ticket_id FROM tickets_v1 "
            "WHERE created_at >= '2026-09-24T00:00:01Z'"
        )
    )
    comparison_result = await comparison_workflow.run(
        request=BusinessAnalyticsRequest(
            question="Filter a UTC timestamp",
            requested_datasets=("tickets_v1",),
        ),
        context=make_context(("tickets_v1",)),
    )
    assert comparison_result.rows == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT amount / 2 AS half_amount FROM orders_v1",
        "SELECT AVG(amount) AS average_amount FROM orders_v1",
        "SELECT CAST(amount AS DOUBLE) AS approximate_amount FROM orders_v1",
        "SELECT CURRENT_DATE FROM orders_v1",
    ],
)
async def test_decimal_division_and_average_fail_closed(sql: str) -> None:
    workflow, _, _, audit, _ = make_stack(sql=sql)
    with pytest.raises(BusinessSQLTranslationError):
        await workflow.run(
            request=BusinessAnalyticsRequest(
                question="Calculate an unsupported decimal operation",
                requested_datasets=("orders_v1",),
            ),
            context=make_context(),
        )
    assert audit.events[-1].status is BusinessAnalyticsAuditStatus.FAILED
    assert audit.events[-1].error_code == "business_sql_translation_failed"


@pytest.mark.anyio
async def test_result_budgets_fail_closed_without_returning_partial_rows(
    tmp_path: Path,
) -> None:
    delivery = tmp_path / "delivery"
    import shutil

    shutil.copytree(FIXTURE, delivery)
    write_orders(delivery, ("1", "2", "3"))
    workflow, _, _, audit, _ = make_stack(
        sql="SELECT status FROM orders_v1 ORDER BY order_id",
        delivery_root=delivery,
        d4_policy=GovernedExecutionPolicy(max_result_rows=1),
    )
    with pytest.raises(BusinessResultLimitError):
        await workflow.run(
            request=BusinessAnalyticsRequest(
                question="List order status",
                requested_datasets=("orders_v1",),
            ),
            context=make_context(),
        )
    assert audit.events[-1].status is BusinessAnalyticsAuditStatus.FAILED
    assert audit.events[-1].row_count is None


@pytest.mark.anyio
async def test_oversized_cells_and_column_counts_are_rejected(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery"
    import shutil

    shutil.copytree(FIXTURE, delivery)
    file_path = delivery / "orders_v1.jsonl"
    record = json.loads(file_path.read_text(encoding="utf-8"))
    record["status"] = "x" * 200
    content = canonical_json_line(record)
    file_path.write_bytes(content)
    manifest_path = delivery / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor = next(
        item for item in manifest["datasets"] if item["dataset_name"] == "orders_v1"
    )
    descriptor["content_sha256"] = hashlib.sha256(content).hexdigest()
    material = {
        "contract_sha256": manifest["schema_sha256"],
        "contract_version": manifest["contract_version"],
        "currency": manifest["currency"],
        "datasets": [
            {
                "content_sha256": item["content_sha256"],
                "dataset_name": item["dataset_name"],
                "row_count": item["row_count"],
            }
            for item in manifest["datasets"]
        ],
        "tenant_id": manifest["tenant_id"],
    }
    manifest["snapshot"]["snapshot_id"] = hashlib.sha256(
        canonical_json(material)
    ).hexdigest()
    manifest_path.write_bytes(canonical_json(manifest))

    cell_budget_workflow, _, _, _, _ = make_stack(
        sql="SELECT status FROM orders_v1",
        delivery_root=delivery,
        d4_policy=GovernedExecutionPolicy(max_result_cell_bytes=64),
    )
    with pytest.raises(BusinessResultLimitError):
        await cell_budget_workflow.run(
            request=BusinessAnalyticsRequest(
                question="Read synthetic status",
                requested_datasets=("orders_v1",),
            ),
            context=make_context(),
        )

    byte_budget_workflow, _, _, _, _ = make_stack(
        sql="SELECT status FROM orders_v1",
        delivery_root=delivery,
        d4_policy=GovernedExecutionPolicy(max_result_bytes=128),
    )
    with pytest.raises(BusinessResultLimitError):
        await byte_budget_workflow.run(
            request=BusinessAnalyticsRequest(
                question="Read a large synthetic status",
                requested_datasets=("orders_v1",),
            ),
            context=make_context(),
        )

    columns_workflow, _, _, _, _ = make_stack(
        sql="SELECT status, amount FROM orders_v1",
        d4_policy=GovernedExecutionPolicy(max_result_columns=1),
    )
    with pytest.raises(BusinessResultLimitError):
        await columns_workflow.run(
            request=BusinessAnalyticsRequest(
                question="Read two synthetic columns",
                requested_datasets=("orders_v1",),
            ),
            context=make_context(),
        )


@pytest.mark.anyio
async def test_execution_rejects_tampered_d3_plan_before_query() -> None:
    workflow, pipeline, executor, _, _ = make_stack(
        sql="SELECT status FROM orders_v1"
    )
    context = make_context()
    plan = await pipeline.generate(
        request=BusinessAnalyticsRequest(
            question="List order status",
            requested_datasets=("orders_v1",),
        ),
        context=context,
    )
    assert "effective_catalog" not in plan.model_dump()
    tampered = plan.model_copy(
        update={
            "generated_sql": "SELECT status FROM tickets_v1 LIMIT 1",
            "referenced_datasets": ("tickets_v1",),
        }
    )

    with pytest.raises((BusinessSQLAuthorizationError, BusinessSQLPolicyViolationError)):
        await executor.execute(
            plan=tampered,
            context=context,
            policy=GovernedExecutionPolicy(),
        )

    altered_catalog = plan.effective_catalog.model_copy(
        update={"contract_sha256": "f" * 64}
    )
    tampered_identity = plan.model_copy(
        update={
            "contract_sha256": "f" * 64,
            "effective_catalog": altered_catalog,
        }
    )
    with pytest.raises(BusinessSQLAuthorizationError):
        await executor.execute(
            plan=tampered_identity,
            context=context,
            policy=GovernedExecutionPolicy(),
        )


def test_duckdb_sandbox_disables_external_access_and_locks_settings() -> None:
    executor = make_stack(sql="SELECT status FROM orders_v1")[2]
    policy = GovernedExecutionPolicy()
    connection = executor._open_sandbox(policy)
    try:
        settings = connection.execute(
            "SELECT current_setting('enable_external_access'), "
            "current_setting('autoload_known_extensions'), "
            "current_setting('autoinstall_known_extensions'), "
            "current_setting('allow_community_extensions'), "
            "current_setting('max_temp_directory_size'), "
            "current_setting('threads')"
        ).fetchone()
        assert settings == (False, False, False, False, "0 bytes", 1)
        for sql in (
            "SELECT * FROM read_csv('outside.csv')",
            "SELECT * FROM read_json('outside.json')",
            "SELECT * FROM read_parquet('outside.parquet')",
            "ATTACH 'outside.duckdb' AS outside",
            "COPY (SELECT 1) TO 'outside.csv'",
            "INSTALL httpfs",
            "LOAD httpfs",
            "SET enable_external_access = true",
            "SET autoload_known_extensions = true",
        ):
            with pytest.raises(Exception):
                connection.execute(sql)
    finally:
        connection.close()


@pytest.mark.anyio
async def test_execution_deadline_calls_interrupt_and_joins_worker() -> None:
    workflow, pipeline, executor, _, _ = make_stack(
        sql="SELECT status FROM orders_v1"
    )
    context = make_context()
    plan = await pipeline.generate(
        request=BusinessAnalyticsRequest(
            question="List order status",
            requested_datasets=("orders_v1",),
        ),
        context=context,
    )
    interrupted = threading.Event()
    closed = threading.Event()

    class FakeConnection:
        def interrupt(self) -> None:
            interrupted.set()

        def close(self) -> None:
            closed.set()

    def controlled_execution(state, plan, catalog, context, policy):
        fake_connection = FakeConnection()
        state["connection"] = fake_connection
        state["query_started_at"] = time.monotonic() - 1
        state["query_started"].set()
        interrupted.wait(2)
        fake_connection.close()
        raise BusinessExecutionTimeoutError()

    executor._execute_sync = controlled_execution  # type: ignore[method-assign]
    with pytest.raises(BusinessExecutionTimeoutError):
        await executor.execute(
            plan=plan,
            context=context,
            policy=GovernedExecutionPolicy(
                max_execution_seconds=0.05,
                cancel_grace_seconds=0.5,
            ),
        )

    assert interrupted.is_set()
    assert closed.is_set()
    assert not any(
        thread.name == "governed-duckdb-query" and thread.is_alive()
        for thread in threading.enumerate()
    )


@pytest.mark.anyio
async def test_async_cancellation_interrupts_and_joins_worker() -> None:
    workflow, pipeline, executor, _, _ = make_stack(
        sql="SELECT status FROM orders_v1"
    )
    context = make_context()
    plan = await pipeline.generate(
        request=BusinessAnalyticsRequest(
            question="List order status",
            requested_datasets=("orders_v1",),
        ),
        context=context,
    )
    ready = threading.Event()
    interrupted = threading.Event()
    closed = threading.Event()

    class FakeConnection:
        def interrupt(self) -> None:
            interrupted.set()

        def close(self) -> None:
            closed.set()

    def controlled_execution(state, plan, catalog, context, policy):
        fake_connection = FakeConnection()
        state["connection"] = fake_connection
        state["query_started_at"] = time.monotonic()
        state["query_started"].set()
        ready.set()
        interrupted.wait(2)
        fake_connection.close()
        raise BusinessExecutionTimeoutError()

    executor._execute_sync = controlled_execution  # type: ignore[method-assign]
    task = asyncio.create_task(
        executor.execute(
            plan=plan,
            context=context,
            policy=GovernedExecutionPolicy(cancel_grace_seconds=0.5),
        )
    )
    assert await asyncio.to_thread(ready.wait, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert interrupted.is_set()
    assert closed.is_set()
    assert not any(
        thread.name == "governed-duckdb-query" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_pinned_duckdb_interrupt_stops_real_long_query() -> None:
    connection_ready = threading.Event()
    query_done = threading.Event()
    state: dict[str, Any] = {}

    def run_query() -> None:
        connection = duckdb.connect(
            ":memory:",
            config={"enable_external_access": False, "threads": "1"},
        )
        state["connection"] = connection
        connection_ready.set()
        try:
            connection.execute("SELECT SUM(HASH(i)) FROM RANGE(1000000000000) t(i)")
        except Exception as error:
            state["error"] = type(error).__name__
        finally:
            connection.close()
            query_done.set()

    worker = threading.Thread(target=run_query, name="duckdb-real-interrupt", daemon=False)
    worker.start()
    assert connection_ready.wait(5)
    time.sleep(0.05)
    state["connection"].interrupt()
    worker.join(3)

    assert not worker.is_alive()
    assert query_done.is_set()
    assert state.get("error") == "InterruptException"


def test_query_fingerprint_and_audit_logger_have_closed_metadata_shapes() -> None:
    with pytest.raises(ValidationError):
        BusinessAnalyticsRequest.model_validate(
            {
                "question": "List orders",
                "delivery_path": "untrusted/path",
            }
        )

    assert set(BusinessAnalyticsAuditEvent.model_fields) == {
        "request_id",
        "tenant_id",
        "authenticated_subject_fingerprint",
        "authorization_grant_id",
        "entrypoint",
        "contract_name",
        "contract_version",
        "catalog_fingerprint",
        "delivery_fingerprint",
        "query_fingerprint",
        "source_engine",
        "execution_engine",
        "status",
        "referenced_datasets",
        "row_count",
        "duration_ms",
        "error_code",
    }
    logger = logging.getLogger("test.business.audit")
    sink = LoggingBusinessAnalyticsAuditSink(logger=logger)
    sink.emit(
        BusinessAnalyticsAuditEvent(
            request_id="request-1",
            tenant_id="tenant-1",
            contract_name="supportops_business_data",
            contract_version="v1",
            status=BusinessAnalyticsAuditStatus.STARTED,
        )
    )


@pytest.mark.anyio
async def test_delivery_failure_is_audited_without_path_or_payload(tmp_path: Path) -> None:
    missing_delivery = tmp_path / "missing"
    audit = InMemoryAuditSink()
    workflow, _, _, _, _ = make_stack(
        sql="SELECT status FROM orders_v1",
        delivery_root=missing_delivery,
        audit=audit,
    )
    with pytest.raises(BusinessDataDeliveryError) as error:
        await workflow.run(
            request=BusinessAnalyticsRequest(
                question="List status",
                requested_datasets=("orders_v1",),
            ),
            context=make_context(),
        )
    assert getattr(error.value, "code", None) == "business_data_delivery_failed"
    failed = audit.events[-1]
    assert failed.status is BusinessAnalyticsAuditStatus.FAILED
    assert failed.error_code == "business_data_delivery_failed"
    assert str(missing_delivery) not in failed.model_dump_json()


@pytest.mark.anyio
async def test_timeout_failure_is_audited_as_a_safe_category() -> None:
    context = make_context()
    audit = InMemoryAuditSink()
    workflow, pipeline, _, _, _ = make_stack(
        sql="SELECT status FROM orders_v1",
        audit=audit,
    )

    class TimedOutExecutor:
        async def execute(self, *, plan, context, policy):
            raise BusinessExecutionTimeoutError()

    timed_workflow = GovernedBusinessAnalyticsWorkflow(
        text2sql_pipeline=pipeline,
        execution_port=TimedOutExecutor(),  # type: ignore[arg-type]
        audit_sink=audit,
    )
    with pytest.raises(BusinessExecutionTimeoutError):
        await timed_workflow.run(
            request=BusinessAnalyticsRequest(
                question="List order status",
                requested_datasets=("orders_v1",),
            ),
            context=context,
        )
    assert audit.events[-1].status is BusinessAnalyticsAuditStatus.FAILED
    assert audit.events[-1].error_code == "business_execution_timeout"
