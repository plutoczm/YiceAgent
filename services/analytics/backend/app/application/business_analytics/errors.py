class BusinessAnalyticsError(Exception):
    """Base class for safe, stable business analytics boundary errors."""

    code = "business_analytics_error"
    safe_message = "Business analytics request could not be prepared"

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class BusinessAnalyticsRequestError(BusinessAnalyticsError):
    code = "invalid_request"
    safe_message = "Business analytics request is invalid"


class BusinessAnalyticsContextError(BusinessAnalyticsError):
    code = "invalid_trusted_context"
    safe_message = "Trusted business analytics context is invalid"


class BusinessCatalogMismatchError(BusinessAnalyticsError):
    code = "catalog_context_mismatch"
    safe_message = "Trusted business catalog does not match its context"


class BusinessCatalogResolutionError(BusinessAnalyticsError):
    code = "catalog_resolution_failed"
    safe_message = "Trusted business catalog could not be resolved"


class BusinessScopeViolationError(BusinessAnalyticsError):
    code = "dataset_scope_violation"
    safe_message = "Requested business dataset scope is not authorized"


class BusinessSchemaRenderError(BusinessAnalyticsError):
    code = "schema_render_failed"
    safe_message = "Managed business schema could not be prepared"


class BusinessSQLGenerationError(BusinessAnalyticsError):
    code = "sql_generation_failed"
    safe_message = "Managed SQL candidate could not be generated"


class BusinessSQLParseError(BusinessAnalyticsError):
    code = "sql_parse_failed"
    safe_message = "Managed SQL candidate could not be parsed safely"


class BusinessSQLAuthorizationError(BusinessAnalyticsError):
    code = "sql_authorization_failed"
    safe_message = "Managed SQL candidate violates the authorized catalog scope"


class BusinessSQLPolicyViolationError(BusinessAnalyticsError):
    code = "sql_policy_violation"
    safe_message = "Managed SQL candidate violates trusted query policy"


class BusinessDataDeliveryError(BusinessAnalyticsError):
    code = "business_data_delivery_failed"
    safe_message = "Trusted business data delivery could not be resolved"


class BusinessDataIntegrityError(BusinessAnalyticsError):
    code = "business_data_integrity_failed"
    safe_message = "Business data delivery failed integrity validation"


class BusinessDataTenantMismatchError(BusinessAnalyticsError):
    code = "business_data_tenant_mismatch"
    safe_message = "Business data delivery does not match the trusted tenant"


class BusinessSQLTranslationError(BusinessAnalyticsError):
    code = "business_sql_translation_failed"
    safe_message = "Authorized SQL is not supported by the execution dialect"


class BusinessExecutionError(BusinessAnalyticsError):
    code = "business_execution_failed"
    safe_message = "Governed business query could not be completed"


class BusinessExecutionTimeoutError(BusinessAnalyticsError):
    code = "business_execution_timeout"
    safe_message = "Governed business query exceeded its execution deadline"


class BusinessResultLimitError(BusinessAnalyticsError):
    code = "business_result_limit_exceeded"
    safe_message = "Governed business query exceeded its result budget"


class BusinessAnalyticsAuditError(BusinessAnalyticsError):
    code = "business_analytics_audit_failed"
    safe_message = "Governed business analytics audit event could not be recorded"
