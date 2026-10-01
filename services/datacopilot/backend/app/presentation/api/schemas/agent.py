from typing import Any, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.application.agent.models import AgentIntent
from backend.app.application.text2sql.models import SQLEngine
from backend.app.domain.entities.memory import (
    LongTermMemory,
    MemoryRule,
    MemoryRuleScope,
)
from backend.app.domain.ports.llm_provider import LLMUsage


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, examples=["统计最近7天活跃用户并检查SQL"])
    user_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    history: list[dict[str, str]] = Field(default_factory=list)
    collection_name: str = "knowledge_base"
    top_k: int = Field(default=5, ge=1, le=20)
    metadata_filter: dict[str, str | int | float | bool] | None = None
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    engine: SQLEngine = SQLEngine.HIVE
    schema_context: str | None = None
    database_name: str | None = None
    use_rag: bool = False
    rag_collection_name: str = "knowledge_base"
    retrieval_mode: str = Field(default="hybrid", pattern="^(vector|hybrid)$")

    @field_validator("engine", mode="before")
    @classmethod
    def normalize_engine(cls, value: Any) -> Any:
        if isinstance(value, str) and value.lower() == "spark":
            return SQLEngine.SPARK_SQL
        return value


class AgentChatResponse(BaseModel):
    intent: AgentIntent
    result: dict[str, Any]
    routing_path: list[str]
    final_response: str
    metadata: dict[str, Any]
    token_usage: LLMUsage


class LongTermMemoryCreate(BaseModel):
    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    content: str = Field(min_length=1, max_length=4000)


class LongTermMemoryList(BaseModel):
    memories: list[LongTermMemory]


class MemoryRuleUpsert(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    scope: MemoryRuleScope = MemoryRuleScope.USER
    user_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_scope_owner(self) -> Self:
        if self.scope is MemoryRuleScope.USER and not self.user_id:
            raise ValueError("user_id is required for user-scoped rules")
        if self.scope is MemoryRuleScope.GLOBAL and self.user_id is not None:
            raise ValueError("global rules must not set user_id")
        return self

    def to_rule(self, rule_id: str) -> MemoryRule:
        return MemoryRule(
            id=rule_id,
            content=self.content,
            scope=self.scope,
            user_id=self.user_id,
            priority=self.priority,
            enabled=self.enabled,
        )


class MemoryRuleList(BaseModel):
    rules: list[MemoryRule]
