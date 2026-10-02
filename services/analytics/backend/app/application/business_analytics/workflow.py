from backend.app.application.business_analytics.errors import (
    BusinessAnalyticsContextError,
    BusinessAnalyticsError,
    BusinessAnalyticsRequestError,
    BusinessCatalogMismatchError,
    BusinessCatalogResolutionError,
    BusinessScopeViolationError,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessAnalyticsRequest,
    BusinessAnalyticsResult,
    BusinessCatalogSnapshot,
)
from backend.app.application.business_analytics.ports import BusinessCatalogPort


class BusinessAnalyticsWorkflow:
    """Prepare an authorized catalog scope without generating or running SQL."""

    def __init__(self, *, catalog_port: BusinessCatalogPort) -> None:
        self._catalog_port = catalog_port

    def prepare(
        self,
        *,
        request: BusinessAnalyticsRequest,
        context: BusinessAnalyticsContext,
    ) -> BusinessAnalyticsResult:
        if not isinstance(request, BusinessAnalyticsRequest):
            raise BusinessAnalyticsRequestError()
        if not isinstance(context, BusinessAnalyticsContext):
            raise BusinessAnalyticsContextError()

        try:
            snapshot = self._catalog_port.resolve(context)
        except BusinessAnalyticsError:
            raise
        except Exception:
            raise BusinessCatalogResolutionError() from None

        if not isinstance(snapshot, BusinessCatalogSnapshot):
            raise BusinessCatalogResolutionError()
        if (
            snapshot.tenant_id != context.tenant_id
            or snapshot.contract_name != context.contract_name
            or snapshot.contract_version != context.contract_version
        ):
            raise BusinessCatalogMismatchError()

        catalog_scope = {dataset.logical_name for dataset in snapshot.datasets}
        trusted_scope = set(context.allowed_datasets)
        if not catalog_scope.issubset(trusted_scope):
            raise BusinessScopeViolationError()

        if request.requested_datasets:
            requested_scope = set(request.requested_datasets)
            if not requested_scope.issubset(trusted_scope):
                raise BusinessScopeViolationError()
            if not requested_scope.issubset(catalog_scope):
                raise BusinessScopeViolationError()
            resolved_datasets = request.requested_datasets
        else:
            resolved_datasets = tuple(
                dataset.logical_name for dataset in snapshot.datasets
            )

        return BusinessAnalyticsResult(
            request_id=context.request_id,
            contract_name=snapshot.contract_name,
            contract_version=snapshot.contract_version,
            catalog_fingerprint=snapshot.catalog_fingerprint,
            resolved_datasets=resolved_datasets,
            catalog_snapshot=snapshot,
        )
