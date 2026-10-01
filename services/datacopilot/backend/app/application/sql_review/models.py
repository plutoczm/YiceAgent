from enum import StrEnum

from pydantic import BaseModel, Field

from backend.app.application.text2sql.models import SQLEngine, Text2SQLResult
from backend.app.domain.ports.llm_provider import LLMUsage


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SQLReviewIssue(BaseModel):
    code: str
    title: str
    description: str
    severity: RiskLevel
    suggestion: str
    category: str = "general"


class SQLReviewResult(BaseModel):
    risk_level: RiskLevel
    score: int = Field(ge=0, le=100)
    issues: list[SQLReviewIssue] = Field(default_factory=list)
    optimization_suggestions: list[str] = Field(default_factory=list)
    llm_explanation: str = ""
    engine: SQLEngine | None = None
    token_usage: LLMUsage = Field(default_factory=LLMUsage)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    def has_issue(self, code: str) -> bool:
        return any(issue.code == code for issue in self.issues)


class GeneratedSQLReviewResult(BaseModel):
    generated_sql: Text2SQLResult
    review: SQLReviewResult
