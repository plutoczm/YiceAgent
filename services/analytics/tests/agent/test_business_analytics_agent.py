import asyncio
from datetime import UTC, datetime

import pytest

from backend.app.application.agent.graph import AgentGraph
from backend.app.application.agent.memory import ConversationMemory
from backend.app.application.agent.models import AgentIntent, AgentRequest
from backend.app.application.agent.business_analytics_adapter import (
    BusinessAnalyticsAgentAdapter,
)
from backend.app.application.agent.router import IntentRouter
from backend.app.application.agent.tools import BusinessAnalyticsAgentToolInput
from backend.app.application.business_analytics.errors import BusinessAnalyticsContextError
from backend.app.application.business_analytics.models import BusinessAnalyticsContext
from backend.app.application.identity.context_factory import BusinessAnalyticsContextFactory
from backend.app.application.identity.models import (
    AgentExecutionContext,
    AuthenticatedPrincipal,
    TenantAccessGrant,
)
from backend.app.application.text2sql.models import (
    SQLEngine,
    SQLValidationResult,
    Text2SQLResult,
)
from backend.app.domain.ports.llm_provider import LLMUsage


class FakeText2SQLService:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, **kwargs) -> Text2SQLResult:
        self.calls += 1
        return Text2SQLResult(
            sql="SELECT id FROM sample_table",
            explanation="generic SQL generation",
            optimization_suggestions=[],
            engine=SQLEngine.HIVE,
            confidence=0.9,
            validation=SQLValidationResult(is_valid=True),
            token_usage=LLMUsage(),
        )


class NeverCalledAdapter:
    async def execute(self, *args, **kwargs):
        raise AssertionError("generic Text2SQL must not invoke the D4 workflow")


class UnusedService:
    async def answer(self, **kwargs):
        raise AssertionError("unused service was invoked")

    async def review(self, **kwargs):
        raise AssertionError("unused service was invoked")

    async def design(self, **kwargs):
        raise AssertionError("unused service was invoked")


def make_graph(*, text2sql_service=None, adapter=None):
    return AgentGraph(
        rag_service=UnusedService(),
        text2sql_service=text2sql_service or FakeText2SQLService(),
        sql_review_service=UnusedService(),
        warehouse_design_service=UnusedService(),
        llm_provider=object(),
        memory=ConversationMemory(),
        business_analytics_adapter=adapter,
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Show order status counts", AgentIntent.BUSINESS_ANALYTICS),
        ("What is the refundable amount total?", AgentIntent.BUSINESS_ANALYTICS),
        ("工单优先级分布", AgentIntent.BUSINESS_ANALYTICS),
        ("Generate SQL using this schema", AgentIntent.TEXT2SQL),
        ("Review SQL: select id from orders", AgentIntent.SQL_REVIEW),
        ("Explain Kafka consumer offsets", AgentIntent.RAG),
    ],
)
def test_deterministic_intent_routing_keeps_managed_data_distinct(question, expected) -> None:
    assert IntentRouter().classify(question).intent is expected


def test_business_analytics_tool_schema_has_only_user_controlled_fields() -> None:
    schema = BusinessAnalyticsAgentToolInput.model_json_schema()

    assert set(schema["properties"]) == {"question", "engine", "requested_datasets"}
    rendered = str(schema).lower()
    for forbidden in (
        "tenant",
        "principal",
        "token",
        "schema",
        "database_url",
        "delivery",
        "credentials",
        "max_rows",
        "timeout",
    ):
        assert forbidden not in rendered


def test_enabled_agent_graph_advertises_closed_business_tool_schema() -> None:
    graph = make_graph(adapter=NeverCalledAdapter())
    tools = graph.tool_catalog()
    business_tool = next(item for item in tools if item["key"] == "business_analytics")

    assert set(business_tool["input_schema"]["properties"]) == {
        "question",
        "engine",
        "requested_datasets",
    }


def test_business_analytics_without_context_fails_closed_and_does_not_fallback() -> None:
    text2sql = FakeText2SQLService()
    graph = make_graph(text2sql_service=text2sql, adapter=NeverCalledAdapter())

    response = asyncio.run(
        graph.run(
            AgentRequest(message="Show order status counts", user_id="tenant-b")
        )
    )

    assert response.intent is AgentIntent.BUSINESS_ANALYTICS
    assert response.result["status"] == "denied"
    assert response.result["code"] == "authenticated_tenant_context_required"
    assert "authenticated tenant access" in response.final_response.lower()
    assert text2sql.calls == 0


def test_generic_text2sql_does_not_invoke_managed_analytics_executor() -> None:
    text2sql = FakeText2SQLService()
    graph = make_graph(text2sql_service=text2sql, adapter=NeverCalledAdapter())

    response = asyncio.run(
        graph.run(AgentRequest(message="Generate SQL using this schema"))
    )

    assert response.intent is AgentIntent.TEXT2SQL
    assert text2sql.calls == 1
    assert response.result["sql"] == "SELECT id FROM sample_table"


def test_user_id_cannot_create_authenticated_execution_context() -> None:
    graph = make_graph(adapter=NeverCalledAdapter())
    assert graph.intent_router.classify("Show order status counts").intent is (
        AgentIntent.BUSINESS_ANALYTICS
    )
    response = asyncio.run(
        graph.run(AgentRequest(message="Show order status counts", user_id="tenant-b"))
    )
    assert response.result["status"] == "denied"


def test_agent_adapter_requires_agent_entrypoint_and_passes_only_request_fields() -> None:
    principal = AuthenticatedPrincipal(
        issuer="https://issuer.test/",
        subject="sub-1",
        audience=("datacopilot-api",),
        authenticated_at=datetime.now(UTC),
    )
    grant = TenantAccessGrant(
        grant_id="a" * 64,
        identity_issuer=principal.issuer,
        identity_subject=principal.subject,
        tenant_id="tenant-a",
        allowed_datasets=("orders_v1",),
        contract_name="supportops_business_data",
        contract_version="v1",
    )
    factory = BusinessAnalyticsContextFactory()
    agent_context = factory.create_agent_context(
        principal=principal,
        grant=grant,
        request_id="c99a8b9a-6dae-4a84-a9a8-d86ea9e5f304",
    )
    api_context = factory.create_context(
        principal=principal,
        grant=grant,
        request_id="c99a8b9a-6dae-4a84-a9a8-d86ea9e5f304",
        entrypoint="api",
    )
    invalid_agent_context = AgentExecutionContext(
        request_id=agent_context.request_id,
        principal=principal,
        tenant_grant=grant,
        business_analytics_context=api_context,
        memory_user_id=agent_context.memory_user_id,
    )

    class CaptureWorkflow:
        async def run(self, *, request, context):
            self.request = request
            self.context = context
            return "complete"

    workflow = CaptureWorkflow()
    adapter = BusinessAnalyticsAgentAdapter(workflow)
    tool_input = BusinessAnalyticsAgentToolInput(
        question="Order status counts",
        requested_datasets=("orders_v1",),
    )

    with pytest.raises(BusinessAnalyticsContextError):
        asyncio.run(adapter.execute(tool_input, execution_context=None))
    with pytest.raises(BusinessAnalyticsContextError):
        asyncio.run(
            adapter.execute(
                tool_input,
                execution_context=invalid_agent_context,
            )
        )
    assert asyncio.run(adapter.execute(tool_input, execution_context=agent_context)) == "complete"
    assert workflow.request.question == "Order status counts"
    assert workflow.request.requested_datasets == ("orders_v1",)
    assert workflow.context.tenant_id == "tenant-a"
