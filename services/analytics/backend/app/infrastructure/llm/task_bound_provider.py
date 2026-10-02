"""按业务任务绑定 LLM provider 的薄适配器。

组合根为每个领域服务注入一层 TaskBoundLLMProvider：调用 provider 前把当前
业务任务写入 ContextVar，供 RoutingLLMProvider 据此分发 local/cloud。
服务文件与测试假件无需任何改动即可获得任务感知路由能力。
"""

from collections.abc import AsyncIterator, Sequence

from backend.app.domain.ports.llm_provider import (
    LLMHealthStatus,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    TaskType,
    current_task,
    llm_task,
)


class TaskBoundLLMProvider:
    def __init__(self, inner, task: TaskType | str) -> None:
        self.inner = inner
        self.task = TaskType(task)

    def provider_name(self) -> str:
        return self.inner.provider_name()

    def token_count(self, text_or_messages: str | Sequence[LLMMessage]) -> int:
        return self.inner.token_count(text_or_messages)

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_type: str | None = None,
    ) -> LLMResponse:
        bound_task = task_type or current_task() or self.task.value
        with llm_task(bound_task):
            return await self.inner.chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                task_type=bound_task,
            )

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_type: str | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        bound_task = task_type or current_task() or self.task.value
        with llm_task(bound_task):
            async for chunk in self.inner.stream_chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                task_type=bound_task,
            ):
                yield chunk

    async def health_check(self) -> LLMHealthStatus:
        return await self.inner.health_check()

    async def aclose(self) -> None:
        closer = getattr(self.inner, "aclose", None)
        if callable(closer):
            await closer()


__all__ = ["TaskBoundLLMProvider"]
