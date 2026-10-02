import pytest

from backend.app.application.agent.memory import ConversationMemory
from backend.app.application.agent.models import AgentRequest
from tests.agent.test_agent_router import make_graph


pytestmark = pytest.mark.anyio


async def test_agent_exposes_structured_tool_schemas() -> None:
    graph = make_graph()

    catalog = graph.tool_catalog()

    assert {item["key"] for item in catalog} == {
        "rag",
        "text2sql",
        "sql_review",
        "warehouse_design",
    }
    rag_tool = next(item for item in catalog if item["key"] == "rag")
    assert "question" in rag_tool["input_schema"]["properties"]
    assert rag_tool["input_schema"]["required"] == ["question"]


async def test_agent_session_memory_accumulates_and_can_be_cleared() -> None:
    memory = ConversationMemory(max_messages=4)
    graph = make_graph()
    graph.memory = memory

    first = await graph.run(AgentRequest(message="介绍一下你", session_id="session-1"))
    second = await graph.run(AgentRequest(message="你好", session_id="session-1"))

    assert first.metadata["memory"]["short_term_messages"] == 2
    assert second.metadata["memory"]["short_term_messages"] == 4
    assert graph.clear_memory("session-1") is True
    assert graph.memory.stats("session-1")["short_term_messages"] == 0


def test_memory_compresses_old_turns_into_summary() -> None:
    memory = ConversationMemory(max_messages=2, summary_chars=200)
    memory.append_turn("session-1", "first question", "first answer")
    memory.append_turn("session-1", "second question", "second answer")

    snapshot = memory.load("session-1")

    assert snapshot.summary
    assert "first question" in snapshot.summary
    assert snapshot.messages[0]["role"] == "system"
