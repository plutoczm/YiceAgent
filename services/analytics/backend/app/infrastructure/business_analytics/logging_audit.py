import logging

from backend.app.application.business_analytics.governed_models import (
    BusinessAnalyticsAuditEvent,
)
from backend.app.application.business_analytics.ports import (
    BusinessAnalyticsAuditSinkPort,
)


class LoggingBusinessAnalyticsAuditSink(BusinessAnalyticsAuditSinkPort):
    """Structured application log adapter, not a durable audit ledger."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("datacopilot.business_analytics.audit")

    def emit(self, event: BusinessAnalyticsAuditEvent) -> None:
        self._logger.info(
            "business_analytics_audit",
            extra={
                "business_analytics_audit": event.model_dump(
                    mode="json",
                    exclude_none=True,
                )
            },
        )
