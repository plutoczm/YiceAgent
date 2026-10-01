from typing import Any, NotRequired, TypedDict

from backend.app.application.agent.models import AgentIntent, AgentRequest
from backend.app.application.identity.models import AgentExecutionContext
from backend.app.application.rag.models import RAGResponse
from backend.app.application.sql_review.models import SQLReviewResult
from backend.app.application.text2sql.models import Text2SQLResult
from backend.app.application.warehouse_design.models import WarehouseDesignResult
from backend.app.domain.ports.llm_provider import LLMUsage


class AgentState(TypedDict):
    request: AgentRequest
    execution_context: NotRequired[AgentExecutionContext | None]
    query: str
    intent: AgentIntent
    confidence: float
    intent_reason: str
    history: list[dict[str, str]]
    memory_context: NotRequired[str]
    routing_path: list[str]
    plan: NotRequired[list[str]]
    retrieved_context: NotRequired[RAGResponse]
    generated_sql: NotRequired[Text2SQLResult]
    business_analytics_result: NotRequired[dict[str, Any]]
    review_result: NotRequired[SQLReviewResult]
    warehouse_design: NotRequired[WarehouseDesignResult]
    general_answer: NotRequired[str]
    result: dict[str, Any]
    final_response: str
    token_usage: LLMUsage
    tool_usage: dict[str, int]
    errors: list[str]
    validation: NotRequired[dict[str, Any]]
