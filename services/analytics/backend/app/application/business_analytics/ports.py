from typing import Protocol

from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessCatalogSnapshot,
)
from backend.app.application.business_analytics.governed_models import (
    BusinessAnalyticsAuditEvent,
    BusinessAnalyticsExecutionData,
    GovernedExecutionPolicy,
)
from backend.app.application.business_analytics.managed_models import (
    ManagedBusinessSQLPlan,
    ManagedSQLDraft,
    ManagedSQLGenerationRequest,
)


class BusinessCatalogPort(Protocol):
    """Resolve an authoritative catalog using server-injected trusted context."""

    def resolve(self, context: BusinessAnalyticsContext) -> BusinessCatalogSnapshot:
        """Return typed metadata already scoped to the trusted request context."""


class ManagedSQLGeneratorPort(Protocol):
    """Generate an untrusted candidate from pipeline-rendered schema only."""

    async def generate(
        self,
        request: ManagedSQLGenerationRequest,
    ) -> ManagedSQLDraft:
        """Return candidate SQL and usage metadata without authorization/execution."""


class GovernedBusinessSQLExecutorPort(Protocol):
    """Resolve, validate, materialize and execute a D3 plan inside infrastructure."""

    async def execute(
        self,
        *,
        plan: ManagedBusinessSQLPlan,
        context: BusinessAnalyticsContext,
        policy: GovernedExecutionPolicy,
    ) -> BusinessAnalyticsExecutionData:
        """Return only a bounded result and safe snapshot metadata."""


class BusinessAnalyticsAuditSinkPort(Protocol):
    """Receive metadata-only governed query audit events."""

    def emit(self, event: BusinessAnalyticsAuditEvent) -> None:
        """Record an audit event without row data or SQL text."""
