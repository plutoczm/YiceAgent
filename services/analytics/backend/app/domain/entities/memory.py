from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class MemoryRuleScope(StrEnum):
    GLOBAL = "global"
    USER = "user"


class MemorySnapshot(BaseModel):
    messages: list[dict[str, str]] = Field(default_factory=list)
    summary: str = ""


class LongTermMemory(BaseModel):
    id: str
    user_id: str
    content: str
    created_at: str
    score: float | None = None


class MemoryRule(BaseModel):
    id: str
    content: str
    scope: MemoryRuleScope = MemoryRuleScope.USER
    user_id: str | None = None
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class AgentMemoryContext(BaseModel):
    short_term: MemorySnapshot = Field(default_factory=MemorySnapshot)
    long_term: list[LongTermMemory] = Field(default_factory=list)
    rules: list[MemoryRule] = Field(default_factory=list)
