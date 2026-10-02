from collections.abc import AsyncIterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field


class TaskType(StrEnum):
    """业务任务类型，用于多 LLM 路由按任务分发 provider。"""

    RAG = "rag"
    TEXT2SQL = "text2sql"
    SQL_REVIEW = "sql_review"
    WAREHOUSE_DESIGN = "warehouse_design"
    GENERAL_CHAT = "general_chat"


_llm_task: ContextVar[str | None] = ContextVar("llm_task", default=None)


@contextmanager
def llm_task(task: TaskType | str):
    """在调用上下文内标记当前业务任务，供 RoutingLLMProvider 读取。"""
    token = _llm_task.set(TaskType(task).value)
    try:
        yield
    finally:
        _llm_task.reset(token)


def current_task() -> str | None:
    return _llm_task.get()


class LLMMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant|tool)$")
    content: str


class LLMUsage(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class LLMResponse(BaseModel):
    provider: str
    model: str
    content: str
    finish_reason: str | None = None
    usage: LLMUsage = Field(default_factory=LLMUsage)
    raw_response_id: str | None = None


class LLMStreamChunk(BaseModel):
    provider: str
    model: str
    content: str
    finish_reason: str | None = None
    usage: LLMUsage | None = None


class LLMHealthStatus(BaseModel):
    provider: str
    ok: bool
    api_key_configured: bool
    reachable: bool
    model_available: bool
    model: str | None = None
    message: str
    latency_ms: float | None = None


class LLMProvider(Protocol):
    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_type: str | None = None,
    ) -> LLMResponse:
        raise NotImplementedError

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_type: str | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        raise NotImplementedError

    async def health_check(self) -> LLMHealthStatus:
        raise NotImplementedError

    def token_count(self, text_or_messages: str | Sequence[LLMMessage]) -> int:
        raise NotImplementedError

    def provider_name(self) -> str:
        raise NotImplementedError
