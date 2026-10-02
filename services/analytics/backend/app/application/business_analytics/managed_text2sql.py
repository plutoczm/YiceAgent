from backend.app.application.business_analytics.errors import (
    BusinessAnalyticsContextError,
    BusinessAnalyticsError,
    BusinessAnalyticsRequestError,
    BusinessSQLGenerationError,
    BusinessSQLPolicyViolationError,
)
from backend.app.application.business_analytics.managed_models import (
    ManagedAnalyticsPolicy,
    ManagedBusinessSQLPlan,
    ManagedGenericValidation,
    ManagedGenericValidationIssue,
    ManagedSQLDraft,
    ManagedSQLGenerationRequest,
    ManagedSQLValidationSummary,
)
from backend.app.application.business_analytics.managed_policy import (
    build_effective_catalog,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessAnalyticsRequest,
)
from backend.app.application.business_analytics.workflow import (
    BusinessAnalyticsWorkflow,
)
from backend.app.application.business_analytics.ports import (
    BusinessCatalogPort,
    ManagedSQLGeneratorPort,
)
from backend.app.application.business_analytics.schema_renderer import (
    BusinessSchemaRenderer,
)
from backend.app.application.business_analytics.sql_authorization import (
    ManagedSQLAuthorizer,
)
from backend.app.application.text2sql.sql_validator import SQLValidator


class ManagedText2SQLPipeline:
    """Generate and authorize a managed SQL plan without executing it."""

    def __init__(
        self,
        *,
        workflow: BusinessAnalyticsWorkflow,
        generator: ManagedSQLGeneratorPort,
        policy: ManagedAnalyticsPolicy | None = None,
        schema_renderer: BusinessSchemaRenderer | None = None,
        sql_validator: SQLValidator | None = None,
        sql_authorizer: ManagedSQLAuthorizer | None = None,
    ) -> None:
        self._workflow = workflow
        self._generator = generator
        self._policy = policy or ManagedAnalyticsPolicy()
        self._schema_renderer = schema_renderer or BusinessSchemaRenderer()
        self._sql_validator = sql_validator or SQLValidator()
        self._sql_authorizer = sql_authorizer or ManagedSQLAuthorizer()

    async def generate(
        self,
        *,
        request: BusinessAnalyticsRequest,
        context: BusinessAnalyticsContext,
    ) -> ManagedBusinessSQLPlan:
        if not isinstance(request, BusinessAnalyticsRequest):
            raise BusinessAnalyticsRequestError()
        if not isinstance(context, BusinessAnalyticsContext):
            raise BusinessAnalyticsContextError()

        prepared = self._workflow.prepare(request=request, context=context)
        effective_catalog = build_effective_catalog(
            prepared,
            engine=request.engine,
            policy=self._policy,
        )
        rendered_schema = self._schema_renderer.render(
            effective_catalog,
            max_chars=self._policy.max_schema_chars,
        )
        generation_request = ManagedSQLGenerationRequest(
            question=request.question,
            engine=request.engine,
            rendered_schema=rendered_schema,
        )
        try:
            draft = await self._generator.generate(generation_request)
        except BusinessAnalyticsError:
            raise
        except Exception:
            raise BusinessSQLGenerationError() from None
        if not isinstance(draft, ManagedSQLDraft):
            raise BusinessSQLGenerationError()
        if len(draft.candidate_sql) > self._policy.max_sql_chars:
            raise BusinessSQLPolicyViolationError()

        self._sql_authorizer.authorize(
            sql=draft.candidate_sql,
            catalog=effective_catalog,
            generic_validation=draft.generic_validation,
        )

        limited_sql = self._sql_validator.enforce_row_limit(
            draft.candidate_sql,
            max_rows=self._policy.max_rows,
        )
        if len(limited_sql) > self._policy.max_sql_chars:
            raise BusinessSQLPolicyViolationError()

        generic_validation = self._sql_validator.validate(
            limited_sql,
            schema=self._schema_renderer.build_validator_schema(effective_catalog),
            engine=request.engine,
        )
        managed_validation = ManagedGenericValidation(
            is_valid=generic_validation.is_valid,
            issues=tuple(
                ManagedGenericValidationIssue(
                    code=issue.code,
                    severity=issue.severity,
                    object_name=issue.object_name,
                )
                for issue in generic_validation.issues
            ),
        )
        evidence = self._sql_authorizer.authorize(
            sql=limited_sql,
            catalog=effective_catalog,
            generic_validation=managed_validation,
            max_rows=self._policy.max_rows,
        )

        validation = ManagedSQLValidationSummary(
            generic_safety_valid=True,
            parse_valid=True,
            read_only=True,
            dataset_scope_valid=True,
            column_scope_valid=True,
            classification_policy_valid=True,
            wildcard_policy_valid=True,
            row_limit_valid=True,
        )
        return ManagedBusinessSQLPlan(
            request_id=prepared.request_id,
            tenant_id=effective_catalog.tenant_id,
            contract_name=effective_catalog.contract_name,
            contract_version=effective_catalog.contract_version,
            contract_sha256=effective_catalog.contract_sha256,
            catalog_fingerprint=effective_catalog.catalog_fingerprint,
            engine=request.engine,
            effective_datasets=tuple(
                dataset.logical_name for dataset in effective_catalog.datasets
            ),
            referenced_datasets=evidence.referenced_datasets,
            referenced_fields=evidence.referenced_fields,
            generated_sql=limited_sql,
            validation=validation,
            max_rows=self._policy.max_rows,
            allowed_classifications=self._policy.allowed_classifications,
            token_usage=draft.token_usage,
            model_explanation=draft.model_explanation,
            effective_catalog=effective_catalog,
        )
