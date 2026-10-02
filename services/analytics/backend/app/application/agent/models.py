from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend.app.application.text2sql.models import SQLEngine
from backend.app.domain.ports.llm_provider import LLMUsage


class AgentIntent(StrEnum):
    RAG = "RAG"
    BUSINESS_ANALYTICS = "BUSINESS_ANALYTICS"
    TEXT2SQL = "TEXT2SQL"
    SQL_REVIEW = "SQL_REVIEW"
    TEXT2SQL_SQL_REVIEW = "TEXT2SQL_SQL_REVIEW"
    WAREHOUSE_DESIGN = "WAREHOUSE_DESIGN"
    GENERAL_CHAT = "GENERAL_CHAT"
    UNKNOWN = "UNKNOWN"


class IntentClassification(BaseModel):
    intent: AgentIntent
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str
    routing_path: list[str] = Field(default_factory=list)
    requires_sql_review: bool = False


class AgentRequest(BaseModel):
    message: str = Field(min_length=1)
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
    requested_datasets: tuple[str, ...] = ()
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


class AgentResponse(BaseModel):
    intent: AgentIntent
    result: dict[str, Any]
    routing_path: list[str] = Field(default_factory=list)
    final_response: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    token_usage: LLMUsage = Field(default_factory=LLMUsage)
