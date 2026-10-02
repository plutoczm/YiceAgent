from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from backend.app.application.business_analytics.errors import (
    BusinessDataIntegrityError,
)
from backend.app.application.business_analytics.governed_models import (
    GovernedExecutionPolicy,
)
from backend.app.application.business_analytics.managed_models import (
    EffectiveBusinessCatalog,
    ManagedAnalyticsPolicy,
)
from backend.app.application.business_analytics.managed_policy import (
    build_effective_catalog,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessAnalyticsRequest,
    BusinessAnalyticsResult,
    BusinessFieldClassification,
    BusinessLogicalType,
)
from backend.app.application.business_analytics.workflow import (
    BusinessAnalyticsWorkflow,
)
from backend.app.application.text2sql.models import SQLEngine
from backend.app.infrastructure.business_analytics.delivery_v2 import (
    BusinessDataDeliveryV2Consumer,
    FileBusinessDataDeliveryResolver,
)
from backend.app.infrastructure.business_analytics.duckdb_materializer import (
    DuckDBSnapshotMaterializer,
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


def make_effective_catalog(
    context: BusinessAnalyticsContext,
    *,
    d3_policy: ManagedAnalyticsPolicy,
    snapshot=None,
) -> tuple[EffectiveBusinessCatalog, BusinessDataContractCatalog]:
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    source = snapshot or catalog_port.resolve(context)
    prepared = BusinessAnalyticsResult(
        request_id=context.request_id,
        contract_name=source.contract_name,
        contract_version=source.contract_version,
        catalog_fingerprint=source.catalog_fingerprint,
        resolved_datasets=("orders_v1",),
        catalog_snapshot=source,
    )
    effective = build_effective_catalog(
        prepared,
        engine=SQLEngine.HIVE,
        policy=d3_policy,
    )
    return effective, catalog_port


def make_consumer(
    catalog_port: BusinessDataContractCatalog,
    delivery_path: Path,
) -> BusinessDataDeliveryV2Consumer:
    return BusinessDataDeliveryV2Consumer(
        delivery_resolver=FileBusinessDataDeliveryResolver(
            tenant_delivery_directories={"tenant-synthetic": delivery_path}
        ),
        contract_catalog=catalog_port,
    )


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


def test_materializer_creates_only_effective_tables_and_fields() -> None:
    context = make_context(allowed_datasets=ALL_DATASETS)
    d3_policy = ManagedAnalyticsPolicy(
        allowed_classifications=(BusinessFieldClassification.INTERNAL,),
    )
    effective, catalog_port = make_effective_catalog(
        context,
        d3_policy=d3_policy,
    )
    consumer = make_consumer(catalog_port, FIXTURE)
    policy = GovernedExecutionPolicy()
    delivery = consumer.validate(context=context, catalog=effective, policy=policy)
    materializer = DuckDBSnapshotMaterializer(delivery_consumer=consumer)
    connection = duckdb.connect(":memory:")
    try:
        materializer.materialize(connection, delivery, effective, policy)
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
            ).fetchall()
        }
        assert table_names == {"orders_v1"}
        fields = {
            row[1]
            for row in connection.execute("PRAGMA table_info('orders_v1')").fetchall()
        }
        assert fields == {
            "status",
            "amount",
            "refundable_amount",
            "refund_status",
            "return_status",
        }
        assert "currency" not in fields
    finally:
        connection.close()


def test_restricted_contract_field_is_not_created() -> None:
    context = make_context()
    catalog_port = BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT)
    source = catalog_port.resolve(context)
    orders = source.datasets[0]
    modified_orders = orders.model_copy(
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
    modified_snapshot = source.model_copy(update={"datasets": (modified_orders,)})
    policy = ManagedAnalyticsPolicy()
    effective, contract_catalog = make_effective_catalog(
        context,
        d3_policy=policy,
        snapshot=modified_snapshot,
    )
    assert "amount" not in {field.name for field in effective.datasets[0].fields}

    consumer = make_consumer(contract_catalog, FIXTURE)
    execution_policy = GovernedExecutionPolicy()
    delivery = consumer.validate(
        context=context,
        catalog=effective,
        policy=execution_policy,
    )
    connection = duckdb.connect(":memory:")
    try:
        DuckDBSnapshotMaterializer(delivery_consumer=consumer).materialize(
            connection,
            delivery,
            effective,
            execution_policy,
        )
        fields = {
            row[1]
            for row in connection.execute("PRAGMA table_info('orders_v1')").fetchall()
        }
        assert "amount" not in fields
    finally:
        connection.close()


def test_second_pass_hash_mismatch_rolls_back_materialized_tables(
    tmp_path: Path,
) -> None:
    delivery_path = tmp_path / "delivery"
    shutil.copytree(FIXTURE, delivery_path)
    context = make_context()
    policy = GovernedExecutionPolicy(materialization_batch_rows=1)
    d3_policy = ManagedAnalyticsPolicy()
    effective, catalog_port = make_effective_catalog(
        context,
        d3_policy=d3_policy,
    )
    consumer = make_consumer(catalog_port, delivery_path)
    delivery = consumer.validate(context=context, catalog=effective, policy=policy)

    path = delivery_path / "orders_v1.jsonl"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["status"] = "changed-after-validation"
    path.write_bytes(canonical_json_line(record))

    connection = duckdb.connect(":memory:")
    try:
        with pytest.raises(BusinessDataIntegrityError):
            DuckDBSnapshotMaterializer(delivery_consumer=consumer).materialize(
                connection,
                delivery,
                effective,
                policy,
            )
        tables = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
        ).fetchall()
        assert tables == []
    finally:
        connection.close()


def test_materializer_exact_decimal_shape_and_type_mapping() -> None:
    materializer = DuckDBSnapshotMaterializer(
        delivery_consumer=make_consumer(
            BusinessDataContractCatalog.from_repository_root(REPOSITORY_ROOT),
            FIXTURE,
        )
    )
    scan = SimpleNamespace(
        decimal_shapes={"amount": SimpleNamespace(precision=38, scale=4)}
    )
    assert materializer._duckdb_type(BusinessLogicalType.DECIMAL, scan, "amount") == (
        "DECIMAL(38,4)"
    )
    assert materializer._duckdb_type(BusinessLogicalType.BOOLEAN, scan, "flag") == "BOOLEAN"
    assert materializer._duckdb_type(BusinessLogicalType.TIMESTAMP, scan, "at") == "TIMESTAMP"
    assert materializer._duckdb_type(BusinessLogicalType.INTEGER, scan, "count") == "BIGINT"
