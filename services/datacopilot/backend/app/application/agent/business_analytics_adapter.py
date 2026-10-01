from backend.app.application.agent.tools import BusinessAnalyticsAgentToolInput
from backend.app.application.business_analytics.errors import (
    BusinessAnalyticsContextError,
)
from backend.app.application.business_analytics.governed_models import (
    GovernedBusinessAnalyticsResult,
)
from backend.app.application.business_analytics.governed_workflow import (
    GovernedBusinessAnalyticsWorkflow,
)
from backend.app.application.business_analytics.models import BusinessAnalyticsRequest
from backend.app.application.identity.models import AgentExecutionContext


class BusinessAnalyticsAgentAdapter:
    """Invoke governed analytics with server context, never model-visible arguments."""

    def __init__(self, workflow: GovernedBusinessAnalyticsWorkflow) -> None:
        self._workflow = workflow

    async def execute(
        self,
        tool_input: BusinessAnalyticsAgentToolInput,
        *,
        execution_context: AgentExecutionContext | None,
    ) -> GovernedBusinessAnalyticsResult:
        if execution_context is None:
            raise BusinessAnalyticsContextError()
        context = execution_context.business_analytics_context
        if context.entrypoint != "agent":
            raise BusinessAnalyticsContextError()
        request = BusinessAnalyticsRequest(
            question=tool_input.question,
            engine=tool_input.engine,
            requested_datasets=tool_input.requested_datasets,
        )
        return await self._workflow.run(request=request, context=context)
