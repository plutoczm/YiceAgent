import re
from typing import Any

from backend.app.application.business_analytics.errors import (
    BusinessDataIntegrityError,
)
from backend.app.application.business_analytics.governed_models import (
    GovernedExecutionPolicy,
)
from backend.app.application.business_analytics.managed_models import (
    EffectiveBusinessCatalog,
)
from backend.app.application.business_analytics.models import (
    BusinessLogicalType,
)
from backend.app.infrastructure.business_analytics.delivery_v2 import (
    BusinessDataDeliveryV2Consumer,
    DecimalShape,
    ValidatedBusinessDelivery,
)


EMPTY_DECIMAL_SHAPE = DecimalShape(precision=38, scale=18)


class DuckDBSnapshotMaterializer:
    """Create only D3 effective tables from validated typed Delivery v2 rows."""

    def __init__(self, *, delivery_consumer: BusinessDataDeliveryV2Consumer) -> None:
        self._delivery_consumer = delivery_consumer

    def materialize(
        self,
        connection: Any,
        delivery: ValidatedBusinessDelivery,
        catalog: EffectiveBusinessCatalog,
        policy: GovernedExecutionPolicy,
    ) -> None:
        accepted_datasets = {
            dataset.name: dataset for dataset in delivery.accepted_contract.contract.datasets
        }
        connection.execute("BEGIN TRANSACTION")
        try:
            for effective_dataset in catalog.datasets:
                source_dataset = accepted_datasets.get(effective_dataset.logical_name)
                scan = delivery.scans.get(effective_dataset.logical_name)
                if source_dataset is None or scan is None:
                    raise BusinessDataIntegrityError()
                fields = tuple(sorted(effective_dataset.fields, key=lambda item: item.name))
                if not fields:
                    raise BusinessDataIntegrityError()
                column_definitions = [
                    f"{self._quote_identifier(field.name)} "
                    f"{self._duckdb_type(field.logical_type, scan, field.name)}"
                    for field in fields
                ]
                table_name = self._quote_identifier(effective_dataset.logical_name)
                connection.execute(
                    f"CREATE TABLE {table_name} ({', '.join(column_definitions)})"
                )
                columns = ", ".join(self._quote_identifier(field.name) for field in fields)
                placeholders = ", ".join("?" for _ in fields)
                insert_sql = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"
                field_names = tuple(field.name for field in fields)
                batch: list[tuple[object, ...]] = []
                for row in self._delivery_consumer.iter_materialization_rows(
                    delivery=delivery,
                    dataset=source_dataset,
                    field_names=field_names,
                    policy=policy,
                ):
                    batch.append(row)
                    if len(batch) >= policy.materialization_batch_rows:
                        connection.executemany(insert_sql, batch)
                        batch.clear()
                if batch:
                    connection.executemany(insert_sql, batch)
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def _duckdb_type(
        self,
        logical_type: BusinessLogicalType,
        scan: Any,
        field_name: str,
    ) -> str:
        if logical_type in {
            BusinessLogicalType.IDENTIFIER,
            BusinessLogicalType.STRING,
            BusinessLogicalType.ENUM,
        }:
            return "VARCHAR"
        if logical_type is BusinessLogicalType.BOOLEAN:
            return "BOOLEAN"
        if logical_type is BusinessLogicalType.TIMESTAMP:
            return "TIMESTAMP"
        if logical_type is BusinessLogicalType.INTEGER:
            return "BIGINT"
        if logical_type is BusinessLogicalType.DECIMAL:
            shape = scan.decimal_shapes.get(field_name, EMPTY_DECIMAL_SHAPE)
            if shape.precision > 38 or shape.scale > shape.precision:
                raise BusinessDataIntegrityError()
            return f"DECIMAL({shape.precision},{shape.scale})"
        raise BusinessDataIntegrityError()

    def _quote_identifier(self, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise BusinessDataIntegrityError()
        return '"' + value.replace('"', '""') + '"'
