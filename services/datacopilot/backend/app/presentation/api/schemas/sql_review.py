from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend.app.application.sql_review.models import (
    GeneratedSQLReviewResult,
    RiskLevel,
    SQLReviewIssue,
)
from backend.app.application.text2sql.models import SQLEngine, Text2SQLResult
from backend.app.domain.ports.llm_provider import LLMUsage


class SQLReviewRequest(BaseModel):
    sql: str = Field(min_length=1, examples=["SELECT * FROM dwd_order_detail"])
    engine: SQLEngine = Field(default=SQLEngine.SPARK_SQL)
    include_llm_explanation: bool = True

    @field_validator("engine", mode="before")
    @classmethod
    def normalize_engine(cls, value: Any) -> Any:
        if isinstance(value, str) and value.lower() == "spark":
            return SQLEngine.SPARK_SQL
        return value


class SQLReviewResponse(BaseModel):
    risk_level: RiskLevel
    score: int = Field(ge=0, le=100)
    issues: list[SQLReviewIssue]
    optimization_suggestions: list[str]
    llm_explanation: str
    engine: SQLEngine | None = None
    token_usage: LLMUsage
    metadata: dict[str, str | int | float | bool]


class GeneratedSQLReviewRequest(BaseModel):
    question: str = Field(min_length=1)
    engine: SQLEngine = Field(default=SQLEngine.HIVE)
    schema_context: str | None = None
    database_name: str | None = None
    use_rag: bool = False

    @field_validator("engine", mode="before")
    @classmethod
    def normalize_engine(cls, value: Any) -> Any:
        if isinstance(value, str) and value.lower() == "spark":
            return SQLEngine.SPARK_SQL
        return value


class GeneratedSQLReviewResponse(GeneratedSQLReviewResult):
    generated_sql: Text2SQLResult
    review: SQLReviewResponse
