from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel

from backend.app.application.business_analytics.errors import (
    BusinessAnalyticsError,
    BusinessDataTenantMismatchError,
    BusinessCatalogResolutionError,
    BusinessExecutionError,
    BusinessExecutionTimeoutError,
    BusinessResultLimitError,
    BusinessSQLAuthorizationError,
    BusinessSQLPolicyViolationError,
    BusinessSQLTranslationError,
)
from backend.app.application.business_analytics.governed_models import (
    BusinessAnalyticsExecutionData,
    GovernedExecutionPolicy,
    GovernedResultColumn,
)
from backend.app.application.business_analytics.managed_models import (
    EffectiveBusinessCatalog,
    ManagedBusinessSQLPlan,
    ManagedBusinessSQLStatus,
    ManagedGenericValidation,
    ManagedGenericValidationIssue,
    ManagedSQLReferencedField,
    ManagedTokenUsage,
    ManagedAnalyticsPolicy,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessAnalyticsResult,
    BusinessFieldClassification,
    CatalogFingerprint,
)
from backend.app.application.business_analytics.ports import (
    BusinessCatalogPort,
    GovernedBusinessSQLExecutorPort,
)
from backend.app.application.business_analytics.managed_policy import (
    build_effective_catalog,
)
from backend.app.application.business_analytics.schema_renderer import (
    BusinessSchemaRenderer,
)
from backend.app.application.business_analytics.sql_authorization import (
    DIALECT_BY_ENGINE,
    ManagedSQLAuthorizer,
)
from backend.app.application.text2sql.sql_validator import SQLValidator
from backend.app.infrastructure.business_analytics.delivery_v2 import (
    BusinessDataDeliveryV2Consumer,
)
from backend.app.infrastructure.business_analytics.duckdb_materializer import (
    DuckDBSnapshotMaterializer,
)


EXECUTION_DIALECT = "duckdb"
RESULT_FETCH_BATCH_ROWS = 64
CLASSIFICATION_RANK = {
    BusinessFieldClassification.PUBLIC: 0,
    BusinessFieldClassification.INTERNAL: 1,
    BusinessFieldClassification.CONFIDENTIAL: 2,
    BusinessFieldClassification.RESTRICTED: 3,
}
SUPPORTED_EXECUTION_FUNCTIONS = {
    "ABS",
    "CAST",
    "COALESCE",
    "COUNT",
    "LENGTH",
    "LOWER",
    "MAX",
    "MIN",
    "NULLIF",
    "SUM",
    "UPPER",
}


class DuckDBBusinessAnalyticsExecutor(GovernedBusinessSQLExecutorPort):
    """Ephemeral DuckDB adapter with a thread-safe interrupt deadline."""

    def __init__(
        self,
        *,
        delivery_consumer: BusinessDataDeliveryV2Consumer,
        catalog_port: BusinessCatalogPort,
        managed_policy: ManagedAnalyticsPolicy,
        authorizer: ManagedSQLAuthorizer | None = None,
        schema_renderer: BusinessSchemaRenderer | None = None,
        sql_validator: SQLValidator | None = None,
    ) -> None:
        self._delivery_consumer = delivery_consumer
        self._materializer = DuckDBSnapshotMaterializer(
            delivery_consumer=delivery_consumer
        )
        self._catalog_port = catalog_port
        self._managed_policy = managed_policy
        self._authorizer = authorizer or ManagedSQLAuthorizer()
        self._schema_renderer = schema_renderer or BusinessSchemaRenderer()
        self._sql_validator = sql_validator or SQLValidator()

    async def execute(
        self,
        *,
        plan: ManagedBusinessSQLPlan,
        context: BusinessAnalyticsContext,
        policy: GovernedExecutionPolicy,
    ) -> BusinessAnalyticsExecutionData:
        self._validate_plan_binding(plan, context, policy)
        trusted_catalog = self._resolve_effective_catalog(plan, context)
        state: dict[str, Any] = {
            "done": threading.Event(),
            "query_started": threading.Event(),
            "cancel_requested": threading.Event(),
        }
        worker = threading.Thread(
            target=self._worker,
            name="governed-duckdb-query",
            args=(state, plan, trusted_catalog, context, policy),
            daemon=False,
        )
        worker.start()

        timed_out = False
        try:
            while not state["done"].is_set():
                started_at = state.get("query_started_at")
                if (
                    started_at is not None
                    and time.monotonic() - started_at >= policy.max_execution_seconds
                ):
                    timed_out = True
                    await self._interrupt_and_join(state, worker, policy)
                    break
                await asyncio.sleep(0.005)
        except asyncio.CancelledError:
            await self._interrupt_and_join(state, worker, policy)
            raise

        worker.join()
        if timed_out:
            raise BusinessExecutionTimeoutError()
        error = state.get("error")
        if error is not None:
            if isinstance(error, BusinessAnalyticsError):
                raise error
            raise BusinessExecutionError() from None
        outcome = state.get("outcome")
        if not isinstance(outcome, BusinessAnalyticsExecutionData):
            raise BusinessExecutionError()
        return outcome

    async def _interrupt_and_join(
        self,
        state: dict[str, Any],
        worker: threading.Thread,
        policy: GovernedExecutionPolicy,
    ) -> None:
        state["cancel_requested"].set()
        connection = state.get("connection")
        if connection is not None and not state["done"].is_set():
            try:
                connection.interrupt()
            except Exception:
                pass
        cancel_deadline = time.monotonic() + policy.cancel_grace_seconds
        while not state["done"].is_set() and time.monotonic() < cancel_deadline:
            await asyncio.sleep(0.005)
        while not state["done"].is_set():
            if connection is not None:
                try:
                    connection.interrupt()
                except Exception:
                    pass
            await asyncio.sleep(0.01)
        worker.join()

    def _worker(
        self,
        state: dict[str, Any],
        plan: ManagedBusinessSQLPlan,
        trusted_catalog: EffectiveBusinessCatalog,
        context: BusinessAnalyticsContext,
        policy: GovernedExecutionPolicy,
    ) -> None:
        try:
            state["outcome"] = self._execute_sync(
                state,
                plan,
                trusted_catalog,
                context,
                policy,
            )
        except BusinessAnalyticsError as error:
            state["error"] = error
        except Exception:
            state["error"] = BusinessExecutionError()
        finally:
            state["done"].set()

    def _execute_sync(
        self,
        state: dict[str, Any],
        plan: ManagedBusinessSQLPlan,
        trusted_catalog: EffectiveBusinessCatalog,
        context: BusinessAnalyticsContext,
        policy: GovernedExecutionPolicy,
    ) -> BusinessAnalyticsExecutionData:
        delivery = self._delivery_consumer.validate(
            context=context,
            catalog=trusted_catalog,
            policy=policy,
        )
        if state["cancel_requested"].is_set():
            raise BusinessExecutionError()
        source_evidence = self._reauthorize_source_plan(
            plan,
            trusted_catalog,
            policy,
        )

        connection = self._open_sandbox(policy)
        state["connection"] = connection
        try:
            self._materializer.materialize(connection, delivery, trusted_catalog, policy)
            if state["cancel_requested"].is_set():
                raise BusinessExecutionError()
            translated_sql, query_fingerprint = self._translate_and_reauthorize(
                plan,
                trusted_catalog,
                policy,
                source_evidence,
            )
            state["query_started_at"] = time.monotonic()
            state["query_started"].set()
            connection.execute(translated_sql)
            columns = self._read_result_columns(connection, policy)
            rows = self._fetch_bounded_result(connection, columns, policy)
            return BusinessAnalyticsExecutionData(
                snapshot=delivery.snapshot,
                source_engine=plan.engine,
                execution_engine="duckdb",
                referenced_datasets=source_evidence.referenced_datasets,
                referenced_fields=source_evidence.referenced_fields,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                result_classification=self._result_classification(
                    trusted_catalog,
                    source_evidence.referenced_datasets,
                    source_evidence.referenced_fields,
                ),
                duration_ms=max(
                    0,
                    int((time.monotonic() - state["query_started_at"]) * 1000),
                ),
                query_fingerprint=query_fingerprint,
                token_usage=plan.token_usage,
                source_plan_reauthorized=True,
                translated_sql_reauthorized=True,
            )
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def _validate_plan_binding(
        self,
        plan: ManagedBusinessSQLPlan,
        context: BusinessAnalyticsContext,
        policy: GovernedExecutionPolicy,
    ) -> None:
        if not isinstance(plan, ManagedBusinessSQLPlan) or not isinstance(
            context,
            BusinessAnalyticsContext,
        ):
            raise BusinessSQLAuthorizationError()
        try:
            catalog = plan.effective_catalog
            if not isinstance(catalog, EffectiveBusinessCatalog):
                raise BusinessSQLAuthorizationError()
            names = tuple(dataset.logical_name for dataset in catalog.datasets)
            invalid = (
                plan.status is not ManagedBusinessSQLStatus.READY
                or plan.request_id != context.request_id
                or plan.tenant_id != context.tenant_id
                or plan.contract_name != context.contract_name
                or plan.contract_version != context.contract_version
                or plan.effective_datasets != names
                or catalog.request_id != context.request_id
                or catalog.tenant_id != context.tenant_id
                or catalog.contract_name != context.contract_name
                or catalog.contract_version != context.contract_version
                or plan.catalog_fingerprint != catalog.catalog_fingerprint
                or plan.contract_sha256 != catalog.contract_sha256
                or plan.engine is not catalog.engine
                or plan.allowed_classifications != catalog.allowed_classifications
                or plan.max_rows > self._managed_policy.max_rows
                or plan.allowed_classifications
                != self._managed_policy.allowed_classifications
                or len(plan.generated_sql) > self._managed_policy.max_sql_chars
                or not set(plan.effective_datasets).issubset(context.allowed_datasets)
                or len(plan.generated_sql) > policy.max_sql_chars
                or plan.max_rows < 1
            )
            if invalid:
                raise BusinessSQLAuthorizationError()
        except BusinessAnalyticsError:
            raise
        except Exception:
            raise BusinessSQLAuthorizationError() from None

    def _resolve_effective_catalog(
        self,
        plan: ManagedBusinessSQLPlan,
        context: BusinessAnalyticsContext,
    ) -> EffectiveBusinessCatalog:
        try:
            snapshot = self._catalog_port.resolve(context)
        except BusinessAnalyticsError:
            raise
        except Exception:
            raise BusinessCatalogResolutionError() from None
        if (
            snapshot.tenant_id != context.tenant_id
            or snapshot.contract_name != context.contract_name
            or snapshot.contract_version != context.contract_version
            or not set(plan.effective_datasets).issubset(
                {dataset.logical_name for dataset in snapshot.datasets}
            )
        ):
            raise BusinessSQLAuthorizationError()
        prepared = BusinessAnalyticsResult(
            request_id=context.request_id,
            contract_name=snapshot.contract_name,
            contract_version=snapshot.contract_version,
            catalog_fingerprint=snapshot.catalog_fingerprint,
            resolved_datasets=plan.effective_datasets,
            catalog_snapshot=snapshot,
        )
        try:
            trusted_catalog = build_effective_catalog(
                prepared,
                engine=plan.engine,
                policy=self._managed_policy,
            )
        except BusinessAnalyticsError:
            raise
        except Exception:
            raise BusinessSQLAuthorizationError() from None
        if trusted_catalog != plan.effective_catalog:
            raise BusinessSQLAuthorizationError()
        return trusted_catalog

    def _reauthorize_source_plan(
        self,
        plan: ManagedBusinessSQLPlan,
        catalog: EffectiveBusinessCatalog,
        policy: GovernedExecutionPolicy,
    ):
        schema = self._schema_renderer.build_validator_schema(catalog)
        generic = self._sql_validator.validate(
            plan.generated_sql,
            schema=schema,
            engine=plan.engine,
        )
        generic_validation = ManagedGenericValidation(
            is_valid=generic.is_valid,
            issues=tuple(
                ManagedGenericValidationIssue(
                    code=issue.code,
                    severity=issue.severity,
                    object_name=issue.object_name,
                )
                for issue in generic.issues
            ),
        )
        evidence = self._authorizer.authorize(
            sql=plan.generated_sql,
            catalog=catalog,
            generic_validation=generic_validation,
            max_rows=plan.max_rows,
        )
        if (
            evidence.referenced_datasets != plan.referenced_datasets
            or evidence.referenced_fields != plan.referenced_fields
        ):
            raise BusinessSQLAuthorizationError()
        if len(plan.generated_sql) > policy.max_sql_chars:
            raise BusinessSQLPolicyViolationError()
        return evidence

    def _translate_and_reauthorize(
        self,
        plan: ManagedBusinessSQLPlan,
        catalog: EffectiveBusinessCatalog,
        policy: GovernedExecutionPolicy,
        source_evidence: Any,
    ) -> tuple[str, str]:
        source_dialect = DIALECT_BY_ENGINE[plan.engine]
        try:
            source_statements = sqlglot.parse(
                plan.generated_sql,
                read=source_dialect,
                error_level=ErrorLevel.RAISE,
            )
            if len(source_statements) != 1 or source_statements[0] is None:
                raise BusinessSQLTranslationError()
            source_ast = source_statements[0]
            self._reject_inexact_decimal_operators(source_ast)
            canonical_sql = source_ast.sql(
                dialect=source_dialect,
                pretty=False,
                comments=False,
            )
            translated = sqlglot.transpile(
                canonical_sql,
                read=source_dialect,
                write=EXECUTION_DIALECT,
                error_level=ErrorLevel.RAISE,
                unsupported_level=ErrorLevel.RAISE,
            )
        except BusinessSQLTranslationError:
            raise
        except Exception:
            raise BusinessSQLTranslationError() from None
        if len(translated) != 1 or not translated[0] or len(translated[0]) > policy.max_sql_chars:
            raise BusinessSQLTranslationError()

        try:
            translated_ast = sqlglot.parse(
                translated[0],
                read=EXECUTION_DIALECT,
                error_level=ErrorLevel.RAISE,
            )
        except Exception:
            raise BusinessSQLTranslationError() from None
        if len(translated_ast) != 1 or translated_ast[0] is None:
            raise BusinessSQLTranslationError()
        self._reject_inexact_decimal_operators(translated_ast[0])
        translated_evidence = self._authorizer.authorize(
            sql=translated[0],
            catalog=catalog,
            generic_validation=ManagedGenericValidation(is_valid=True),
            max_rows=plan.max_rows,
            dialect_override=EXECUTION_DIALECT,
        )
        if (
            translated_evidence.referenced_datasets
            != source_evidence.referenced_datasets
            or translated_evidence.referenced_fields
            != source_evidence.referenced_fields
        ):
            raise BusinessSQLAuthorizationError()

        fingerprint_material = "\n".join(
            (
                "datacopilot-query-v1",
                plan.engine.value,
                plan.contract_sha256,
                plan.catalog_fingerprint,
                str(plan.max_rows),
                canonical_sql,
            )
        ).encode("utf-8")
        return translated[0], hashlib.sha256(fingerprint_material).hexdigest()

    def _reject_inexact_decimal_operators(self, expression: exp.Expression) -> None:
        if any(isinstance(node, (exp.Div, exp.IntDiv, exp.Avg)) for node in expression.walk()):
            raise BusinessSQLTranslationError()
        for node in expression.walk():
            if (
                isinstance(node, exp.Func)
                and not isinstance(node, (exp.Binary, exp.Unary, exp.Predicate))
                and node.sql_name().upper() not in SUPPORTED_EXECUTION_FUNCTIONS
            ):
                raise BusinessSQLTranslationError()
            if isinstance(node, exp.Cast):
                target = node.args.get("to")
                if isinstance(target, exp.DataType) and target.this in {
                    exp.DataType.Type.FLOAT,
                    exp.DataType.Type.DOUBLE,
                }:
                    raise BusinessSQLTranslationError()

    def _open_sandbox(self, policy: GovernedExecutionPolicy):
        connection = None
        try:
            connection = duckdb.connect(
                ":memory:",
                config={
                    "enable_external_access": False,
                    "memory_limit": f"{policy.duckdb_memory_limit_bytes}B",
                    "threads": str(policy.duckdb_threads),
                },
            )
            for setting in (
                "SET autoload_known_extensions = false",
                "SET autoinstall_known_extensions = false",
                "SET allow_community_extensions = false",
                "SET TimeZone = 'UTC'",
                "SET max_temp_directory_size = '0B'",
            ):
                connection.execute(setting)
            connection.execute("SET lock_configuration = true")
            observed = connection.execute(
                "SELECT current_setting('enable_external_access'), "
                "current_setting('autoload_known_extensions'), "
                "current_setting('autoinstall_known_extensions'), "
                "current_setting('allow_community_extensions'), "
                "current_setting('max_temp_directory_size'), "
                "current_setting('TimeZone'), current_setting('threads')"
            ).fetchone()
            expected = (
                False,
                False,
                False,
                False,
                "0 bytes",
                "UTC",
                policy.duckdb_threads,
            )
            if observed != expected:
                raise BusinessExecutionError()
            return connection
        except BusinessAnalyticsError:
            if connection is not None:
                connection.close()
            raise
        except Exception:
            if connection is not None:
                connection.close()
            raise BusinessExecutionError() from None

    def _fetch_bounded_result(
        self,
        connection: Any,
        columns: tuple[GovernedResultColumn, ...],
        policy: GovernedExecutionPolicy,
    ) -> tuple[tuple[object, ...], ...]:
        if len(columns) > policy.max_result_columns:
            raise BusinessResultLimitError()
        rows: list[tuple[object, ...]] = []
        serialized_columns = [
            {"name": column.name, "data_type": column.data_type}
            for column in columns
        ]
        while True:
            remaining = policy.max_result_rows + 1 - len(rows)
            if remaining <= 0:
                raise BusinessResultLimitError()
            batch = connection.fetchmany(min(RESULT_FETCH_BATCH_ROWS, remaining))
            if not batch:
                break
            for raw_row in batch:
                normalized = tuple(self._normalize_scalar(value) for value in raw_row)
                if len(normalized) != len(columns):
                    raise BusinessExecutionError()
                if len(rows) >= policy.max_result_rows:
                    raise BusinessResultLimitError()
                for value in normalized:
                    cell_size = len(
                        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
                    )
                    if cell_size > policy.max_result_cell_bytes:
                        raise BusinessResultLimitError()
                rows.append(normalized)
                payload = {
                    "columns": serialized_columns,
                    "rows": rows,
                }
                serialized_size = len(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                if serialized_size > policy.max_result_bytes:
                    raise BusinessResultLimitError()
        return tuple(rows)

    def _read_result_columns(
        self,
        connection: Any,
        policy: GovernedExecutionPolicy,
    ) -> tuple[GovernedResultColumn, ...]:
        description = connection.description or ()
        if len(description) > policy.max_result_columns:
            raise BusinessResultLimitError()
        try:
            return tuple(
                GovernedResultColumn(
                    name=str(column[0]),
                    data_type=str(column[1]),
                )
                for column in description
            )
        except Exception:
            raise BusinessExecutionError() from None

    def _normalize_scalar(self, value: object) -> object:
        if value is None or type(value) is bool or type(value) is int or isinstance(value, str):
            return value
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise BusinessExecutionError()
            if value.is_zero():
                return "0"
            return format(value, "f")
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                value = value.replace(tzinfo=UTC)
            normalized = value.astimezone(UTC)
            timespec = "microseconds" if normalized.microsecond else "seconds"
            return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")
        if isinstance(value, date):
            return value.isoformat()
        raise BusinessExecutionError()

    def _result_classification(
        self,
        catalog: EffectiveBusinessCatalog,
        referenced_datasets: tuple[str, ...],
        referenced_fields: tuple[ManagedSQLReferencedField, ...],
    ) -> BusinessFieldClassification:
        datasets = {dataset.logical_name: dataset for dataset in catalog.datasets}
        field_classifications = [
            next(
                field.classification
                for field in datasets[reference.dataset_name].fields
                if field.name == reference.field_name
            )
            for reference in referenced_fields
        ]
        if field_classifications:
            classifications = field_classifications
        else:
            classifications = [
                datasets[name].classification or BusinessFieldClassification.INTERNAL
                for name in referenced_datasets
            ]
        if not classifications:
            return BusinessFieldClassification.PUBLIC
        return max(classifications, key=CLASSIFICATION_RANK.__getitem__)
