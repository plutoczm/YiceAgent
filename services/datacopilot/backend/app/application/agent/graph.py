import re
import time
from collections.abc import AsyncIterator
from typing import Any

from langgraph.graph import END, StateGraph

from backend.app.application.agent.exceptions import AgentExecutionError
from backend.app.application.agent.models import (
    AgentIntent,
    AgentRequest,
    AgentResponse,
)
from backend.app.application.agent.memory import ConversationMemory
from backend.app.application.agent.nodes import AgentNodes
from backend.app.application.agent.router import IntentRouter
from backend.app.application.agent.state import AgentState
from backend.app.application.agent.tools import AgentToolbox
from backend.app.application.agent.tools import BusinessAnalyticsAgentToolInput
from backend.app.application.agent.business_analytics_adapter import (
    BusinessAnalyticsAgentAdapter,
)
from backend.app.application.identity.models import AgentExecutionContext
from backend.app.application.rag.rag_service import RAGService
from backend.app.application.sql_review.sql_review_service import SQLReviewService
from backend.app.application.text2sql.text2sql_service import Text2SQLService
from backend.app.application.warehouse_design.design_service import WarehouseDesignService
from backend.app.core.logger import get_logger
from backend.app.domain.entities.memory import LongTermMemory, MemoryRule
from backend.app.domain.ports.agent_memory import AgentMemory
from backend.app.domain.ports.llm_provider import LLMProvider, LLMUsage


class AgentGraph:
    def __init__(
        self,
        *,
        rag_service: RAGService,
        text2sql_service: Text2SQLService,
        sql_review_service: SQLReviewService,
        warehouse_design_service: WarehouseDesignService,
        llm_provider: LLMProvider,
        intent_router: IntentRouter | None = None,
        memory: AgentMemory | None = None,
        business_analytics_adapter: BusinessAnalyticsAgentAdapter | None = None,
        max_steps: int = 8,
    ) -> None:
        self.intent_router = intent_router or IntentRouter()
        self.toolbox = AgentToolbox(
            rag_service=rag_service,
            text2sql_service=text2sql_service,
            sql_review_service=sql_review_service,
            warehouse_design_service=warehouse_design_service,
        )
        self.nodes = AgentNodes(
            intent_router=self.intent_router,
            rag_service=rag_service,
            text2sql_service=text2sql_service,
            sql_review_service=sql_review_service,
            warehouse_design_service=warehouse_design_service,
            llm_provider=llm_provider,
            toolbox=self.toolbox,
            business_analytics_adapter=business_analytics_adapter,
        )
        self.memory = memory or ConversationMemory()
        self.max_steps = max_steps
        self.logger = get_logger("datacopilot.agent")
        self._graph = self._build_graph()

    async def run(
        self,
        request: AgentRequest,
        *,
        execution_context: AgentExecutionContext | None = None,
    ) -> AgentResponse:
        started_at = time.perf_counter()
        if execution_context is not None:
            request = request.model_copy(
                update={"user_id": execution_context.memory_user_id}
            )
        memory_stats: dict[str, Any] = {}
        memory_errors: list[str] = []
        memory_messages: list[dict[str, str]] = []
        memory_context = ""
        try:
            if request.session_id:
                snapshot = self.memory.load(
                    request.session_id,
                    user_id=request.user_id,
                )
                memory_messages.extend(snapshot.messages)
                memory_stats = self.memory.stats(
                    request.session_id,
                    user_id=request.user_id,
                )
            rules = self.memory.list_rules(request.user_id)
            long_term = (
                await self.memory.recall_long_term(request.user_id, request.message)
                if request.user_id
                else []
            )
            context_messages, memory_context = self._memory_context(rules, long_term)
            memory_messages = [*context_messages, *memory_messages]
            memory_stats.update(
                {
                    "rules_loaded": len(rules),
                    "long_term_memories_recalled": len(long_term),
                }
            )
        except Exception as exc:
            self.logger.exception("Agent memory load failed")
            memory_errors.append(f"memory_load_failed:{type(exc).__name__}")
            memory_stats = {"available": False}
        request = request.model_copy(
            update={"history": [*memory_messages, *request.history]}
        )
        initial_state = self._initial_state(
            request,
            memory_context,
            memory_errors,
            execution_context=execution_context,
        )
        try:
            state = await self._graph.ainvoke(
                initial_state,
                config={"recursion_limit": self.max_steps},
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 3)
            self.logger.exception(
                "Agent workflow failed",
                extra={
                    "intent": initial_state["intent"].value,
                    "routing_path": initial_state["routing_path"],
                    "execution_time_ms": elapsed_ms,
                    "tool_usage": initial_state["tool_usage"],
                    "errors": [str(exc)],
                },
            )
            raise AgentExecutionError("Agent workflow failed") from exc
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 3)
        response = AgentResponse(
            intent=state["intent"],
            result=state["result"],
            routing_path=state["routing_path"],
            final_response=state["final_response"],
            token_usage=state["token_usage"],
            metadata={
                "intent_confidence": state["confidence"],
                "intent_reason": state["intent_reason"],
                "execution_time_ms": elapsed_ms,
                "tool_usage": state["tool_usage"],
                "errors": state["errors"],
                "plan": state.get("plan", []),
                "max_steps": self.max_steps,
                "memory": memory_stats,
                "validation": state.get("validation", {}).get("summary", {}),
            },
        )
        if execution_context is not None:
            response.metadata["request_id"] = execution_context.request_id
        if request.session_id:
            try:
                self.memory.append_turn(
                    request.session_id,
                    request.message,
                    response.final_response,
                    user_id=request.user_id,
                )
                self.memory.save_state(
                    request.session_id,
                    {
                        "intent": response.intent.value,
                        "routing_path": response.routing_path,
                        "plan": response.metadata.get("plan", []),
                        "result": response.result,
                        "final_response": response.final_response,
                        "token_usage": response.token_usage.model_dump(mode="json"),
                        "errors": response.metadata.get("errors", []),
                    },
                    user_id=request.user_id,
                )
                response.metadata["memory"] = self.memory.stats(
                    request.session_id,
                    user_id=request.user_id,
                ) | {
                    "rules_loaded": memory_stats.get("rules_loaded", 0),
                    "long_term_memories_recalled": memory_stats.get(
                        "long_term_memories_recalled", 0
                    ),
                }
            except Exception as exc:
                self.logger.exception("Agent memory save failed")
                response.metadata["errors"] = [
                    *response.metadata.get("errors", []),
                    f"memory_save_failed:{type(exc).__name__}",
                ]
                response.metadata["memory"] = {"available": False}
        self.logger.info(
            "Agent workflow completed",
            extra={
                "intent": response.intent.value,
                "routing_path": response.routing_path,
                "execution_time_ms": elapsed_ms,
                "tool_usage": response.metadata["tool_usage"],
                "errors": response.metadata["errors"],
            },
        )
        return response

    async def stream(
        self,
        request: AgentRequest,
        *,
        execution_context: AgentExecutionContext | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        response = await self.run(request, execution_context=execution_context)
        yield {
            "event": "metadata",
            "data": {
                "intent": response.intent.value,
                "routing_path": response.routing_path,
                "metadata": response.metadata,
            },
        }
        for chunk in self._stream_chunks(response.final_response):
            yield {"event": "token", "data": {"text": chunk}}
        yield {
            "event": "result",
            "data": {
                "intent": response.intent.value,
                "result": response.result,
                "token_usage": response.token_usage.model_dump(mode="json"),
            },
        }
        yield {"event": "done", "data": {"status": "complete"}}

    def tool_catalog(self) -> list[dict[str, Any]]:
        catalog = self.toolbox.catalog()
        if self.nodes.business_analytics_adapter is not None:
            catalog.append(
                {
                    "key": "business_analytics",
                    "name": "run_governed_business_analytics",
                    "description": (
                        "Run bounded analytics over the authenticated tenant's "
                        "approved business datasets."
                    ),
                    "input_schema": BusinessAnalyticsAgentToolInput.model_json_schema(),
                }
            )
        return catalog

    def clear_memory(self, session_id: str, *, user_id: str | None = None) -> bool:
        return self.memory.clear(session_id, user_id=user_id)

    def load_session_state(
        self, session_id: str, *, user_id: str | None = None
    ) -> dict:
        return self.memory.load_state(session_id, user_id=user_id)

    async def add_long_term_memory(
        self, user_id: str, content: str
    ) -> LongTermMemory:
        return await self.memory.add_long_term(user_id, content)

    async def list_long_term_memories(self, user_id: str) -> list[LongTermMemory]:
        return await self.memory.list_long_term(user_id)

    def delete_long_term_memory(self, user_id: str, memory_id: str) -> bool:
        return self.memory.delete_long_term(user_id, memory_id)

    def list_memory_rules(self, user_id: str | None = None) -> list[MemoryRule]:
        return self.memory.list_rules(user_id)

    def upsert_memory_rule(self, rule: MemoryRule) -> MemoryRule:
        return self.memory.upsert_rule(rule)

    def delete_memory_rule(
        self, rule_id: str, *, user_id: str | None = None
    ) -> bool:
        return self.memory.delete_rule(rule_id, user_id=user_id)

    def _stream_chunks(self, text: str, *, target_size: int = 48) -> list[str]:
        parts = re.findall(r"\S+\s*|\s+", text)
        if not parts:
            return [text]
        chunks: list[str] = []
        current = ""
        for part in parts:
            current += part
            if len(current) >= target_size:
                chunks.append(current)
                current = ""
        if current:
            chunks.append(current)
        return chunks

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("classify_intent", self.nodes.classify_intent)
        graph.add_node("planner", self.nodes.planner)
        graph.add_node("rag", self.nodes.rag)
        graph.add_node("business_analytics", self.nodes.business_analytics)
        graph.add_node("text2sql", self.nodes.text2sql)
        graph.add_node("sql_review", self.nodes.sql_review)
        graph.add_node("warehouse_design", self.nodes.warehouse_design)
        graph.add_node("general_chat", self.nodes.general_chat)
        graph.add_node("unknown", self.nodes.unknown)
        graph.add_node("validate_result", self.nodes.validate_result)
        graph.add_node("format_response", self.nodes.format_response)

        graph.set_entry_point("classify_intent")
        graph.add_edge("classify_intent", "planner")
        graph.add_conditional_edges(
            "planner",
            self._route_from_classification,
            {
                "rag": "rag",
                "business_analytics": "business_analytics",
                "text2sql": "text2sql",
                "sql_review": "sql_review",
                "warehouse_design": "warehouse_design",
                "general_chat": "general_chat",
                "unknown": "unknown",
            },
        )
        graph.add_conditional_edges(
            "text2sql",
            self._route_after_text2sql,
            {
                "sql_review": "sql_review",
                "validate_result": "validate_result",
            },
        )
        for node_name in (
            "rag",
            "business_analytics",
            "sql_review",
            "warehouse_design",
            "general_chat",
            "unknown",
        ):
            graph.add_edge(node_name, "validate_result")
        graph.add_edge("validate_result", "format_response")
        graph.add_edge("format_response", END)
        return graph.compile()

    def _route_from_classification(self, state: AgentState) -> str:
        return self.intent_router.route_key(state["intent"])

    def _route_after_text2sql(self, state: AgentState) -> str:
        return self.intent_router.route_after_text2sql(state["intent"])

    def _initial_state(
        self,
        request: AgentRequest,
        memory_context: str = "",
        memory_errors: list[str] | None = None,
        *,
        execution_context: AgentExecutionContext | None = None,
    ) -> AgentState:
        return {
            "request": request,
            "execution_context": execution_context,
            "query": request.message,
            "intent": AgentIntent.UNKNOWN,
            "confidence": 0.0,
            "intent_reason": "",
            "history": request.history,
            "memory_context": memory_context,
            "routing_path": [],
            "result": {},
            "final_response": "",
            "token_usage": LLMUsage(),
            "tool_usage": {},
            "errors": list(memory_errors or []),
            "validation": {},
        }

    def _memory_context(
        self,
        rules: list[MemoryRule],
        memories: list[LongTermMemory],
    ) -> tuple[list[dict[str, str]], str]:
        sections: list[str] = []
        if rules:
            sections.append(
                "Persistent rules (follow unless they conflict with safety policies):\n"
                + "\n".join(f"- {rule.content}" for rule in rules)
            )
        if memories:
            sections.append(
                "Relevant explicit user memories (may be stale; do not invent details):\n"
                + "\n".join(f"- {memory.content}" for memory in memories)
            )
        context = "\n\n".join(sections)
        return (
            [{"role": "system", "content": context}] if context else [],
            context,
        )
