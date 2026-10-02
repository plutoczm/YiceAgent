import asyncio
import time

from backend.app.application.business_analytics.errors import (
    BusinessAnalyticsAuditError,
    BusinessAnalyticsContextError,
    BusinessAnalyticsError,
    BusinessAnalyticsRequestError,
    BusinessDataTenantMismatchError,
    BusinessExecutionError,
    BusinessResultLimitError,
    BusinessSQLAuthorizationError,
)
from backend.app.application.business_analytics.governed_models import (
    BusinessAnalyticsAuditEvent,
    BusinessAnalyticsAuditStatus,
    BusinessAnalyticsExecutionData,
    GovernedBusinessAnalyticsResult,
    GovernedExecutionPolicy,
)
from backend.app.application.business_analytics.managed_models import (
    ManagedBusinessSQLPlan,
    ManagedBusinessSQLStatus,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessAnalyticsRequest,
)
from backend.app.application.business_analytics.ports import (
    BusinessAnalyticsAuditSinkPort,
    GovernedBusinessSQLExecutorPort,
)
from backend.app.application.business_analytics.managed_text2sql import (
    ManagedText2SQLPipeline,
)


class GovernedBusinessAnalyticsWorkflow:
    """Internal D1-D4 workflow; delivery and SQL paths come from trusted ports."""

    def __init__(
        self,
        *,
        text2sql_pipeline: ManagedText2SQLPipeline,
        execution_port: GovernedBusinessSQLExecutorPort,
        audit_sink: BusinessAnalyticsAuditSinkPort,
        policy: GovernedExecutionPolicy | None = None,
    ) -> None:
        self._text2sql_pipeline = text2sql_pipeline
        self._execution_port = execution_port
        self._audit_sink = audit_sink
        self._policy = policy or GovernedExecutionPolicy()

    async def run(
        self,
        *,
        request: BusinessAnalyticsRequest,
        context: BusinessAnalyticsContext,
    ) -> GovernedBusinessAnalyticsResult:
        if not isinstance(request, BusinessAnalyticsRequest):
            raise BusinessAnalyticsRequestError()
        if not isinstance(context, BusinessAnalyticsContext):
            raise BusinessAnalyticsContextError()

        started = time.monotonic()
        plan: ManagedBusinessSQLPlan | None = None
        try:
            plan = await self._text2sql_pipeline.generate(
                request=request,
                context=context,
            )
            if plan.status is not ManagedBusinessSQLStatus.READY:
                raise BusinessExecutionError()

            self._emit(
                BusinessAnalyticsAuditEvent(
                    request_id=context.request_id,
                    tenant_id=context.tenant_id,
                    authenticated_subject_fingerprint=(
                        context.authenticated_subject_fingerprint
                    ),
                    authorization_grant_id=context.authorization_grant_id,
                    entrypoint=context.entrypoint,
                    contract_name=plan.contract_name,
                    contract_version=plan.contract_version,
                    catalog_fingerprint=plan.catalog_fingerprint,
                    query_fingerprint=None,
                    source_engine=plan.engine,
                    status=BusinessAnalyticsAuditStatus.STARTED,
                    referenced_datasets=plan.referenced_datasets,
                )
            )

            execution = await self._execution_port.execute(
                plan=plan,
                context=context,
                policy=self._policy,
            )
            self._validate_execution_binding(plan, context, execution)
            if (
                execution.row_count > self._policy.max_result_rows
                or execution.row_count > plan.max_rows
                or len(execution.columns) > self._policy.max_result_columns
                or len(execution.rows) != execution.row_count
            ):
                raise BusinessResultLimitError()

            result = GovernedBusinessAnalyticsResult(
                request_id=context.request_id,
                tenant_id=context.tenant_id,
                contract_name=plan.contract_name,
                contract_version=plan.contract_version,
                contract_sha256=plan.contract_sha256,
                catalog_fingerprint=plan.catalog_fingerprint,
                snapshot=execution.snapshot,
                source_engine=plan.engine,
                execution_engine=execution.execution_engine,
                effective_datasets=plan.effective_datasets,
                referenced_datasets=execution.referenced_datasets,
                referenced_fields=execution.referenced_fields,
                columns=execution.columns,
                rows=execution.rows,
                row_count=execution.row_count,
                result_classification=execution.result_classification,
                duration_ms=execution.duration_ms,
                query_fingerprint=execution.query_fingerprint,
                token_usage=plan.token_usage,
                source_plan_reauthorized=execution.source_plan_reauthorized,
                translated_sql_reauthorized=execution.translated_sql_reauthorized,
            )
            self._emit(
                BusinessAnalyticsAuditEvent(
                    request_id=context.request_id,
                    tenant_id=context.tenant_id,
                    authenticated_subject_fingerprint=(
                        context.authenticated_subject_fingerprint
                    ),
                    authorization_grant_id=context.authorization_grant_id,
                    entrypoint=context.entrypoint,
                    contract_name=plan.contract_name,
                    contract_version=plan.contract_version,
                    catalog_fingerprint=plan.catalog_fingerprint,
                    delivery_fingerprint=execution.snapshot.delivery_fingerprint,
                    query_fingerprint=execution.query_fingerprint,
                    source_engine=plan.engine,
                    execution_engine=execution.execution_engine,
                    status=BusinessAnalyticsAuditStatus.EXECUTED,
                    referenced_datasets=execution.referenced_datasets,
                    row_count=execution.row_count,
                    duration_ms=execution.duration_ms,
                )
            )
            return result
        except BusinessAnalyticsError as error:
            self._emit_failure(context, plan, error.code, started)
            raise
        except asyncio.CancelledError:
            self._emit_failure(context, plan, "business_execution_cancelled", started)
            raise
        except Exception:
            self._emit_failure(context, plan, BusinessExecutionError.code, started)
            raise BusinessExecutionError() from None

    def _validate_execution_binding(
        self,
        plan: ManagedBusinessSQLPlan,
        context: BusinessAnalyticsContext,
        execution: BusinessAnalyticsExecutionData,
    ) -> None:
        if not isinstance(execution, BusinessAnalyticsExecutionData):
            raise BusinessExecutionError()
        snapshot = execution.snapshot
        if snapshot.tenant_id != context.tenant_id:
            raise BusinessDataTenantMismatchError()
        if (
            snapshot.contract_name != plan.contract_name
            or snapshot.contract_version != plan.contract_version
            or snapshot.contract_sha256 != plan.contract_sha256
            or snapshot.effective_datasets != plan.effective_datasets
            or execution.source_engine is not plan.engine
            or not execution.source_plan_reauthorized
            or not execution.translated_sql_reauthorized
            or not set(execution.referenced_datasets).issubset(
                set(plan.effective_datasets)
            )
            or any(
                field.dataset_name not in plan.effective_datasets
                for field in execution.referenced_fields
            )
        ):
            raise BusinessSQLAuthorizationError()

    def _emit_failure(
        self,
        context: BusinessAnalyticsContext,
        plan: ManagedBusinessSQLPlan | None,
        error_code: str,
        started: float,
    ) -> None:
        try:
            self._emit(
                BusinessAnalyticsAuditEvent(
                    request_id=context.request_id,
                    tenant_id=context.tenant_id,
                    authenticated_subject_fingerprint=(
                        context.authenticated_subject_fingerprint
                    ),
                    authorization_grant_id=context.authorization_grant_id,
                    entrypoint=context.entrypoint,
                    contract_name=(plan.contract_name if plan else context.contract_name),
                    contract_version=(
                        plan.contract_version if plan else context.contract_version
                    ),
                    catalog_fingerprint=(plan.catalog_fingerprint if plan else None),
                    query_fingerprint=None,
                    source_engine=(plan.engine if plan else None),
                    status=BusinessAnalyticsAuditStatus.FAILED,
                    referenced_datasets=(plan.referenced_datasets if plan else ()),
                    duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                    error_code=error_code,
                )
            )
        except BusinessAnalyticsAuditError:
            raise

    def _emit(self, event: BusinessAnalyticsAuditEvent) -> None:
        try:
            self._audit_sink.emit(event)
        except Exception:
            raise BusinessAnalyticsAuditError() from None
