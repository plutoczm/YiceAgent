from collections.abc import AsyncIterator, Sequence

import pytest
from fastapi.testclient import TestClient

from backend.app.application.agent.exceptions import AgentExecutionError
from backend.app.application.agent.graph import AgentGraph
from backend.app.application.agent.models import AgentIntent, AgentRequest, AgentResponse
from backend.app.application.agent.router import IntentRouter
from backend.app.application.rag.models import Citation, RAGResponse
from backend.app.application.sql_review.models import (
    RiskLevel,
    SQLReviewIssue,
    SQLReviewResult,
)
from backend.app.application.text2sql.models import (
    SQLEngine,
    SQLValidationResult,
    Text2SQLResult,
)
from backend.app.application.warehouse_design.models import WarehouseDesignResult
from backend.app.domain.ports.llm_provider import (
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    LLMUsage,
)
from backend.app.domain.entities.memory import MemoryRule
from backend.app.main import create_app
from backend.app.presentation.api.dependencies.providers import get_agent_graph


pytestmark = pytest.mark.anyio


class FakeRAGService:
    async def answer(self, question: str, **kwargs) -> RAGResponse:
        return RAGResponse(
            answer=f"RAG answer for {question}",
            retrieved_chunks=[],
            citations=[
                Citation(
                    document_name="spark.md",
                    chunk_reference="spark.md#chunk-0",
                    similarity_score=0.97,
                    source_metadata={
                        "document_id": "doc-1",
                        "filename": "spark.md",
                        "file_type": "markdown",
                        "domain": "spark",
                        "chunk_index": 0,
                        "created_at": "2026-06-08T00:00:00Z",
                        "source": "spark.md",
                        "tags": ["spark"],
                    },
                )
            ],
            metadata={"retrieved_count": 1},
            token_usage=LLMUsage(prompt_tokens=5, completion_tokens=7, total_tokens=12),
        )


class FakeText2SQLService:
    async def generate(self, **kwargs) -> Text2SQLResult:
        return Text2SQLResult(
            sql=(
                "SELECT COUNT(DISTINCT user_id) AS active_users "
                "FROM dwd_user_behavior_detail "
                "WHERE dt >= date_sub(current_date, 7)"
            ),
            explanation="Count distinct active users in the last 7 days.",
            optimization_suggestions=["Use dt partition pruning."],
            engine=kwargs.get("engine", SQLEngine.HIVE),
            confidence=0.92,
            validation=SQLValidationResult(is_valid=True),
            token_usage=LLMUsage(prompt_tokens=20, completion_tokens=15, total_tokens=35),
            metadata={"schema_table_count": 0},
        )


class FakeSQLReviewService:
    async def review(self, **kwargs) -> SQLReviewResult:
        sql = kwargs["sql"]
        issues = []
        if "SELECT *" in sql.upper():
            issues.append(
                SQLReviewIssue(
                    code="select_star",
                    title="SELECT * detected",
                    description="Avoid SELECT * for production workloads.",
                    severity=RiskLevel.MEDIUM,
                    suggestion="Select only required columns.",
                )
            )
        return SQLReviewResult(
            risk_level=RiskLevel.MEDIUM if issues else RiskLevel.LOW,
            score=72 if issues else 94,
            issues=issues,
            optimization_suggestions=["Review partition filters and scanned columns."],
            llm_explanation="The SQL review completed with rule-based guidance.",
            engine=kwargs.get("engine", SQLEngine.HIVE),
            token_usage=LLMUsage(prompt_tokens=10, completion_tokens=8, total_tokens=18),
        )


class FakeWarehouseDesignService:
    async def design(self, **kwargs) -> WarehouseDesignResult:
        return WarehouseDesignResult.example(kwargs["requirement"])


class FakeLLMProvider:
    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        return LLMResponse(
            provider="fake",
            model="fake-agent",
            content="I am DataPilot-AI, your data engineering copilot.",
            usage=LLMUsage(prompt_tokens=8, completion_tokens=10, total_tokens=18),
        )

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        yield LLMStreamChunk(provider="fake", model="fake-agent", content="I am ")
        yield LLMStreamChunk(
            provider="fake",
            model="fake-agent",
            content="DataPilot-AI.",
            finish_reason="stop",
            usage=LLMUsage(prompt_tokens=8, completion_tokens=4, total_tokens=12),
        )

    def token_count(self, text_or_messages):
        return 0

    def provider_name(self) -> str:
        return "fake"


def make_graph() -> AgentGraph:
    return AgentGraph(
        rag_service=FakeRAGService(),
        text2sql_service=FakeText2SQLService(),
        sql_review_service=FakeSQLReviewService(),
        warehouse_design_service=FakeWarehouseDesignService(),
        llm_provider=FakeLLMProvider(),
    )


def test_intent_router_classifies_supported_intents() -> None:
    router = IntentRouter()

    assert router.classify("什么是Spark AQE").intent is AgentIntent.RAG
    assert router.classify("统计最近7天活跃用户").intent is AgentIntent.TEXT2SQL
    assert router.classify("帮我检查这段SQL: SELECT * FROM dwd_order_detail").intent is AgentIntent.SQL_REVIEW
    assert router.classify("设计电商订单数仓").intent is AgentIntent.WAREHOUSE_DESIGN
    assert router.classify("介绍一下你").intent is AgentIntent.GENERAL_CHAT
    assert router.classify("asdf qwer zxcv").intent is AgentIntent.UNKNOWN


async def test_agent_graph_routes_rag_query() -> None:
    response = await make_graph().run(AgentRequest(message="什么是Spark AQE"))

    assert response.intent is AgentIntent.RAG
    assert response.routing_path == ["classify_intent", "planner", "rag", "format_response"]
    assert response.metadata["plan"] == [
        "query_knowledge_base",
        "validate_result",
        "format_response",
    ]
    assert response.result["answer"].startswith("RAG answer")
    assert response.result["citations"][0]["document_name"] == "spark.md"
    assert response.final_response.startswith("RAG answer")


async def test_agent_graph_routes_text2sql_query() -> None:
    response = await make_graph().run(
        AgentRequest(
            message="统计最近7天活跃用户",
            engine=SQLEngine.HIVE,
            schema_context="dwd_user_behavior_detail(user_id bigint, dt string)",
        )
    )

    assert response.intent is AgentIntent.TEXT2SQL
    assert response.routing_path == ["classify_intent", "planner", "text2sql", "format_response"]
    assert "COUNT(DISTINCT user_id)" in response.result["sql"]
    assert response.result["validation"]["is_valid"] is True


async def test_agent_graph_routes_sql_review_query() -> None:
    response = await make_graph().run(
        AgentRequest(
            message="帮我检查这段SQL: SELECT * FROM dwd_order_detail",
            engine=SQLEngine.SPARK_SQL,
        )
    )

    assert response.intent is AgentIntent.SQL_REVIEW
    assert response.routing_path == ["classify_intent", "planner", "sql_review", "format_response"]
    assert response.result["risk_level"] == "MEDIUM"
    assert response.result["issues"][0]["code"] == "select_star"


async def test_agent_graph_routes_warehouse_design_query() -> None:
    response = await make_graph().run(AgentRequest(message="设计电商订单数仓"))

    assert response.intent is AgentIntent.WAREHOUSE_DESIGN
    assert response.routing_path == ["classify_intent", "planner", "warehouse_design", "format_response"]
    assert response.result["ods"]
    assert response.result["dwd"]
    assert response.result["metrics"]
    assert response.result["ddl"]


async def test_agent_graph_handles_general_chat_with_llm() -> None:
    response = await make_graph().run(AgentRequest(message="介绍一下你"))

    assert response.intent is AgentIntent.GENERAL_CHAT
    assert response.routing_path == ["classify_intent", "planner", "general_chat", "format_response"]
    assert "DataPilot-AI" in response.final_response
    assert response.result["answer"] == response.final_response


async def test_agent_graph_runs_text2sql_then_sql_review_for_chained_request() -> None:
    response = await make_graph().run(
        AgentRequest(
            message="统计最近7天活跃用户并检查SQL",
            engine=SQLEngine.HIVE,
            schema_context="dwd_user_behavior_detail(user_id bigint, dt string)",
        )
    )

    assert response.intent is AgentIntent.TEXT2SQL_SQL_REVIEW
    assert response.routing_path == [
        "classify_intent",
        "planner",
        "text2sql",
        "sql_review",
        "format_response",
    ]
    assert response.result["generated_sql"]["sql"].startswith("SELECT")
    assert response.result["review"]["risk_level"] == "LOW"


async def test_agent_graph_streams_events() -> None:
    events = [
        event
        async for event in make_graph().stream(
            AgentRequest(message="统计最近7天活跃用户并检查SQL")
        )
    ]

    event_names = [event["event"] for event in events]
    assert event_names[0] == "metadata"
    assert event_names[-2:] == ["result", "done"]
    assert event_names.count("token") > 1
    streamed_text = "".join(
        event["data"]["text"] for event in events if event["event"] == "token"
    )
    assert "Generated SQL" in streamed_text
    assert events[0]["data"]["intent"] == "TEXT2SQL_SQL_REVIEW"
    result_event = next(event for event in events if event["event"] == "result")
    assert "generated_sql" in result_event["data"]["result"]


def test_agent_api_returns_structured_response() -> None:
    app = create_app()
    app.dependency_overrides[get_agent_graph] = make_graph
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/v1/agent/chat",
        json={
            "message": "统计最近7天活跃用户并检查SQL",
            "engine": "hive",
            "schema_context": "dwd_user_behavior_detail(user_id bigint, dt string)",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "TEXT2SQL_SQL_REVIEW"
    assert payload["routing_path"] == [
        "classify_intent",
        "planner",
        "text2sql",
        "sql_review",
        "format_response",
    ]
    assert payload["result"]["generated_sql"]["sql"].startswith("SELECT")
    assert payload["metadata"]["tool_usage"]["text2sql"] == 1
    assert payload["metadata"]["tool_usage"]["sql_review"] == 1


def test_agent_stream_api_returns_sse_events() -> None:
    app = create_app()
    app.dependency_overrides[get_agent_graph] = make_graph
    client = TestClient(app, raise_server_exceptions=False)

    with client.stream(
        "POST",
        "/api/v1/agent/chat/stream",
        json={"message": "介绍一下你"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: metadata" in body
    assert "GENERAL_CHAT" in body
    assert "event: token" in body
    assert "DataPilot-AI" in body
    assert "event: result" in body
    assert "event: done" in body


def test_agent_stream_api_emits_error_event_after_workflow_failure() -> None:
    class BrokenStreamingGraph:
        async def stream(self, request):
            if False:
                yield None
            raise AgentExecutionError("Agent workflow failed")

    app = create_app()
    app.dependency_overrides[get_agent_graph] = lambda: BrokenStreamingGraph()
    client = TestClient(app, raise_server_exceptions=False)

    with client.stream(
        "POST",
        "/api/v1/agent/chat/stream",
        json={"message": "介绍一下你"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: error" in body
    assert '"code": "agent_error"' in body
    assert "event: done" in body
    assert '"status": "failed"' in body


def test_agent_api_returns_consistent_error_for_agent_failures() -> None:
    class BrokenGraph:
        async def run(self, request):
            raise AgentExecutionError("Agent workflow failed")

    app = create_app()
    app.dependency_overrides[get_agent_graph] = lambda: BrokenGraph()
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/v1/agent/chat", json={"message": "介绍一下你"})

    assert response.status_code == 500
    payload = response.json()
    assert payload["error"]["code"] == "agent_error"
    assert payload["error"]["message"] == "Agent workflow failed"


def test_agent_response_schema_allows_unknown_intent() -> None:
    response = AgentResponse(
        intent=AgentIntent.UNKNOWN,
        routing_path=["classify_intent", "planner", "unknown", "format_response"],
        result={"answer": "I am not sure which DataPilot-AI workflow to use."},
        final_response="I am not sure which DataPilot-AI workflow to use.",
    )

    assert response.intent is AgentIntent.UNKNOWN
    assert response.result["answer"].startswith("I am not sure")


def test_agent_memory_api_manages_long_term_and_rule_memory() -> None:
    graph = make_graph()
    app = create_app()
    app.dependency_overrides[get_agent_graph] = lambda: graph
    client = TestClient(app, raise_server_exceptions=False)

    created = client.post(
        "/api/v1/agent/memory/long-term",
        json={"user_id": "user-1", "content": "I prefer Spark SQL."},
    )
    assert created.status_code == 201
    memory_id = created.json()["id"]

    listed = client.get("/api/v1/agent/memory/long-term/user-1")
    assert listed.status_code == 200
    assert listed.json()["memories"][0]["content"] == "I prefer Spark SQL."

    rule = client.put(
        "/api/v1/agent/memory/rules/prefer-spark",
        json={
            "content": "Prefer Spark SQL.",
            "scope": "user",
            "user_id": "user-1",
            "priority": 500,
        },
    )
    assert rule.status_code == 200

    rules = client.get("/api/v1/agent/memory/rules", params={"user_id": "user-1"})
    assert rules.json()["rules"][0]["id"] == "prefer-spark"

    deleted_memory = client.delete(
        f"/api/v1/agent/memory/long-term/user-1/{memory_id}"
    )
    deleted_rule = client.delete(
        "/api/v1/agent/memory/rules/prefer-spark",
        params={"user_id": "user-1"},
    )
    assert deleted_memory.json()["deleted"] is True
    assert deleted_rule.json()["deleted"] is True


async def test_agent_loads_long_term_and_rule_memory_into_metadata() -> None:
    graph = make_graph()
    await graph.add_long_term_memory("user-1", "Spark SQL is preferred")
    graph.upsert_memory_rule(
        MemoryRule(
            id="safe-rule",
            content="Never generate write SQL.",
            scope="user",
            user_id="user-1",
            priority=1000,
        )
    )

    response = await graph.run(
        AgentRequest(
            message="介绍一下你",
            user_id="user-1",
            session_id="session-memory",
        )
    )

    assert response.metadata["memory"]["rules_loaded"] == 1
    assert response.metadata["memory"]["long_term_memories_recalled"] == 1
    checkpoint = graph.load_session_state(
        "session-memory",
        user_id="user-1",
    )
    assert checkpoint["intent"] == "GENERAL_CHAT"
    assert checkpoint["routing_path"] == response.routing_path
