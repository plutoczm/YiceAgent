from collections.abc import Sequence
from typing import Any

from backend.app.application.agent.models import AgentIntent
from backend.app.application.agent.router import IntentRouter
from backend.app.application.agent.state import AgentState
from backend.app.application.agent.tools import (
    AgentToolbox,
    BusinessAnalyticsAgentToolInput,
)
from backend.app.application.agent.business_analytics_adapter import (
    BusinessAnalyticsAgentAdapter,
)
from backend.app.application.business_analytics.errors import BusinessAnalyticsError
from backend.app.application.agent.validation import ResultValidator
from backend.app.application.rag.rag_service import RAGService
from backend.app.application.sql_review.sql_review_service import SQLReviewService
from backend.app.application.text2sql.text2sql_service import Text2SQLService
from backend.app.application.warehouse_design.design_service import WarehouseDesignService
from backend.app.domain.ports.llm_provider import LLMMessage, LLMProvider, LLMUsage


class AgentNodes:
    def __init__(
        self,
        *,
        intent_router: IntentRouter,
        rag_service: RAGService,
        text2sql_service: Text2SQLService,
        sql_review_service: SQLReviewService,
        warehouse_design_service: WarehouseDesignService,
        llm_provider: LLMProvider,
        toolbox: AgentToolbox | None = None,
        business_analytics_adapter: BusinessAnalyticsAgentAdapter | None = None,
        result_validator: ResultValidator | None = None,
    ) -> None:
        self.intent_router = intent_router
        self.rag_service = rag_service
        self.text2sql_service = text2sql_service
        self.sql_review_service = sql_review_service
        self.warehouse_design_service = warehouse_design_service
        self.llm_provider = llm_provider
        self.result_validator = result_validator or ResultValidator()
        self.toolbox = toolbox or AgentToolbox(
            rag_service=rag_service,
            text2sql_service=text2sql_service,
            sql_review_service=sql_review_service,
            warehouse_design_service=warehouse_design_service,
        )
        self.business_analytics_adapter = business_analytics_adapter

    async def classify_intent(self, state: AgentState) -> dict[str, Any]:
        classification = self.intent_router.classify(state["query"])
        return {
            "intent": classification.intent,
            "confidence": classification.confidence,
            "intent_reason": classification.reason,
            "routing_path": state["routing_path"] + ["classify_intent"],
        }

    async def planner(self, state: AgentState) -> dict[str, Any]:
        steps_by_intent = {
            AgentIntent.RAG: ["query_knowledge_base"],
            AgentIntent.BUSINESS_ANALYTICS: ["run_governed_business_analytics"],
            AgentIntent.TEXT2SQL: ["generate_sql"],
            AgentIntent.SQL_REVIEW: ["review_sql"],
            AgentIntent.TEXT2SQL_SQL_REVIEW: ["generate_sql", "review_sql"],
            AgentIntent.WAREHOUSE_DESIGN: ["design_data_warehouse"],
            AgentIntent.GENERAL_CHAT: ["general_chat"],
            AgentIntent.UNKNOWN: ["fallback_response"],
        }
        return {
            "plan": [
                *steps_by_intent[state["intent"]],
                "validate_result",
                "format_response",
            ],
            "routing_path": state["routing_path"] + ["planner"],
        }

    async def rag(self, state: AgentState) -> dict[str, Any]:
        request = state["request"]
        response = await self.toolbox.invoke(
            "rag",
            {
                "question": state["query"],
                "collection_name": request.collection_name,
                "top_k": request.top_k,
                "metadata_filter": request.metadata_filter,
                "score_threshold": request.score_threshold,
                "retrieval_mode": request.retrieval_mode,
            },
        )
        return {
            "retrieved_context": response,
            "routing_path": state["routing_path"] + ["rag"],
            "token_usage": self._add_usage(state["token_usage"], response.token_usage),
            "tool_usage": self._increment_tool(state["tool_usage"], "rag"),
        }

    async def text2sql(self, state: AgentState) -> dict[str, Any]:
        request = state["request"]
        response = await self.toolbox.invoke(
            "text2sql",
            {
                "question": self._with_memory_context(state, state["query"]),
                "engine": request.engine,
                "schema_context": request.schema_context,
                "database_name": request.database_name,
                "use_rag": request.use_rag,
                "rag_collection_name": request.rag_collection_name,
            },
        )
        return {
            "generated_sql": response,
            "routing_path": state["routing_path"] + ["text2sql"],
            "token_usage": self._add_usage(state["token_usage"], response.token_usage),
            "tool_usage": self._increment_tool(state["tool_usage"], "text2sql"),
        }

    async def business_analytics(self, state: AgentState) -> dict[str, Any]:
        context = state.get("execution_context")
        if self.business_analytics_adapter is None or context is None:
            data = {
                "status": "denied",
                "code": "authenticated_tenant_context_required",
            }
        else:
            request = state["request"]
            try:
                tool_input = BusinessAnalyticsAgentToolInput(
                    question=state["query"],
                    engine=request.engine,
                    requested_datasets=request.requested_datasets,
                )
                result = await self.business_analytics_adapter.execute(
                    tool_input,
                    execution_context=context,
                )
                data = {
                    "status": result.status.value,
                    "columns": [column.model_dump(mode="json") for column in result.columns],
                    "rows": [list(row) for row in result.rows],
                    "row_count": result.row_count,
                    "currency": result.snapshot.currency,
                    "freshness": [
                        dataset.model_dump(mode="json")
                        for dataset in result.snapshot.datasets
                    ],
                    "result_classification": result.result_classification.value,
                    "query_fingerprint": result.query_fingerprint,
                    "contract_version": result.contract_version,
                    "snapshot_id": result.snapshot.snapshot_id,
                }
            except BusinessAnalyticsError as exc:
                data = {"status": "failed", "code": exc.code}
            except Exception:
                data = {"status": "failed", "code": "business_analytics_unavailable"}
        return {
            "business_analytics_result": data,
            "routing_path": state["routing_path"] + ["business_analytics"],
            "tool_usage": self._increment_tool(state["tool_usage"], "business_analytics"),
        }

    async def sql_review(self, state: AgentState) -> dict[str, Any]:
        request = state["request"]
        sql = self._extract_sql(state)
        response = await self.toolbox.invoke(
            "sql_review",
            {
                "sql": sql,
                "engine": request.engine,
                "include_llm_explanation": True,
            },
        )
        return {
            "review_result": response,
            "routing_path": state["routing_path"] + ["sql_review"],
            "token_usage": self._add_usage(state["token_usage"], response.token_usage),
            "tool_usage": self._increment_tool(state["tool_usage"], "sql_review"),
        }

    async def warehouse_design(self, state: AgentState) -> dict[str, Any]:
        request = state["request"]
        response = await self.toolbox.invoke(
            "warehouse_design",
            {
                "requirement": self._with_memory_context(state, state["query"]),
                "use_rag": request.use_rag,
                "rag_collection_name": request.rag_collection_name,
            },
        )
        return {
            "warehouse_design": response,
            "routing_path": state["routing_path"] + ["warehouse_design"],
            "token_usage": self._add_usage(state["token_usage"], response.token_usage),
            "tool_usage": self._increment_tool(state["tool_usage"], "warehouse_design"),
        }

    async def general_chat(self, state: AgentState) -> dict[str, Any]:
        response = await self.llm_provider.chat(
            self._general_chat_messages(state),
            temperature=0.2,
            max_tokens=600,
        )
        return {
            "general_answer": response.content.strip(),
            "routing_path": state["routing_path"] + ["general_chat"],
            "token_usage": self._add_usage(state["token_usage"], response.usage),
            "tool_usage": self._increment_tool(state["tool_usage"], "general_chat"),
        }

    async def unknown(self, state: AgentState) -> dict[str, Any]:
        return {
            "general_answer": (
                "I am not sure which DataPilot-AI workflow to use. "
                "You can ask about knowledge base topics, SQL generation, SQL review, "
                "or warehouse design."
            ),
            "routing_path": state["routing_path"] + ["unknown"],
        }

    async def validate_result(self, state: AgentState) -> dict[str, Any]:
        validation = self.result_validator.validate(state)
        return {"validation": validation.model_dump(mode="json")}

    async def format_response(self, state: AgentState) -> dict[str, Any]:
        intent = state["intent"]
        result: dict[str, Any]
        final_response: str
        if intent is AgentIntent.RAG:
            rag_response = state["retrieved_context"]
            result = rag_response.model_dump(mode="json")
            final_response = rag_response.answer
        elif intent is AgentIntent.BUSINESS_ANALYTICS:
            result = dict(state["business_analytics_result"])
            if result.get("status") != "executed":
                final_response = "Business analytics requires authenticated tenant access."
            else:
                columns = [column["name"] for column in result["columns"]]
                final_response = (
                    f"Data result: {result['row_count']} row(s), "
                    f"currency {result['currency']}, columns {columns}. "
                    "Bounded rows and snapshot freshness are in the structured result."
                )
        elif intent is AgentIntent.TEXT2SQL:
            generated = state["generated_sql"]
            result = generated.model_dump(mode="json")
            final_response = self._format_text2sql(generated.sql, generated.explanation)
        elif intent is AgentIntent.SQL_REVIEW:
            review = state["review_result"]
            result = review.model_dump(mode="json")
            final_response = self._format_sql_review(review.risk_level.value, review.score)
        elif intent is AgentIntent.TEXT2SQL_SQL_REVIEW:
            generated = state["generated_sql"]
            review = state["review_result"]
            result = {
                "generated_sql": generated.model_dump(mode="json"),
                "review": review.model_dump(mode="json"),
            }
            final_response = "\n\n".join(
                [
                    self._format_text2sql(generated.sql, generated.explanation),
                    self._format_sql_review(review.risk_level.value, review.score),
                ]
            )
        elif intent is AgentIntent.WAREHOUSE_DESIGN:
            design = state["warehouse_design"]
            result = design.model_dump(mode="json")
            final_response = (
                f"Warehouse design generated with {len(design.ods)} ODS, "
                f"{len(design.dwd)} DWD, {len(design.dws)} DWS, and "
                f"{len(design.ads)} ADS tables."
            )
        else:
            answer = state.get("general_answer", "")
            result = {"answer": answer}
            final_response = answer
        validation = state.get("validation", {})
        if validation and "validation" not in result:
            result["validation"] = validation
        return {
            "result": result,
            "final_response": final_response,
            "routing_path": state["routing_path"] + ["format_response"],
            "validation": validation,
        }

    def _extract_sql(self, state: AgentState) -> str:
        generated = state.get("generated_sql")
        if generated is not None:
            return generated.sql
        query = state["query"]
        marker = ":"
        if marker in query:
            return query.split(marker, 1)[1].strip()
        chinese_marker = "："
        if chinese_marker in query:
            return query.split(chinese_marker, 1)[1].strip()
        return query

    def _general_chat_messages(self, state: AgentState) -> Sequence[LLMMessage]:
        history = [
            LLMMessage(role=item.get("role", "user"), content=item.get("content", ""))
            for item in state["history"]
            if item.get("role") in {"user", "assistant", "system"}
            and item.get("content")
        ]
        return [
            LLMMessage(
                role="system",
                content=(
                    "You are DataPilot-AI, an AI data engineering copilot. "
                    "Briefly explain your capabilities without claiming unavailable tools."
                ),
            ),
            *history[-6:],
            LLMMessage(role="user", content=state["query"]),
        ]

    def _increment_tool(self, tool_usage: dict[str, int], tool_name: str) -> dict[str, int]:
        updated = dict(tool_usage)
        updated[tool_name] = updated.get(tool_name, 0) + 1
        return updated

    def _add_usage(self, left: LLMUsage, right: LLMUsage | None) -> LLMUsage:
        if right is None:
            return left
        return LLMUsage(
            prompt_tokens=left.prompt_tokens + right.prompt_tokens,
            completion_tokens=left.completion_tokens + right.completion_tokens,
            total_tokens=left.total_tokens + right.total_tokens,
        )

    def _format_text2sql(self, sql: str, explanation: str) -> str:
        return f"Generated SQL:\n```sql\n{sql}\n```\n\n{explanation}"

    def _format_sql_review(self, risk_level: str, score: int) -> str:
        return f"SQL review completed. Risk level: {risk_level}. Score: {score}/100."

    def _with_memory_context(self, state: AgentState, value: str) -> str:
        context = state.get("memory_context", "")
        if not context:
            return value
        return f"{value}\n\nAgent memory context:\n{context}"
