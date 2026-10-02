from pydantic import BaseModel, Field

from backend.app.application.text2sql.models import (
    SQLEngine,
    SQLValidationResult,
)
from backend.app.domain.ports.llm_provider import LLMUsage


class Text2SQLRequest(BaseModel):
    question: str = Field(min_length=1, examples=["统计最近7天活跃用户数"])
    engine: SQLEngine = Field(default=SQLEngine.HIVE)
    schema_context: str | None = Field(default=None)
    database_name: str | None = Field(default=None)
    use_rag: bool = Field(default=False)
    rag_collection_name: str = Field(default="knowledge_base")


class Text2SQLResponse(BaseModel):
    sql: str
    explanation: str
    optimization_suggestions: list[str]
    validation: SQLValidationResult
    engine: SQLEngine
    confidence: float = Field(ge=0.0, le=1.0)
    token_usage: LLMUsage
    metadata: dict[str, str | int | float | bool]
