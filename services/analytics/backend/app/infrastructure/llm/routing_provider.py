"""多 LLM 路由 provider。

按业务任务（通过显式 task_type 或 TaskBoundLLMProvider 写入的 ContextVar 读取）
在 local（本地微调模型）与 cloud（DeepSeek/OpenAI 云端）之间分发请求：
- 专业数据工程任务（Text2SQL/SQL 审查/数仓设计）→ local
- 通用需求（RAG/通用对话）→ cloud
本地 provider 失败时可自动降级到云端，保证模型未导入或 Ollama 未启动时
专业任务不中断，实现生产级优雅降级。
"""

from collections.abc import AsyncIterator, Sequence

from backend.app.domain.ports.llm_provider import (
    LLMHealthStatus,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    TaskType,
    current_task,
)
from backend.app.infrastructure.llm.exceptions import LLMProviderError


DEFAULT_LOCAL_TASKS = frozenset(
    {
        TaskType.TEXT2SQL.value,
        TaskType.SQL_REVIEW.value,
        TaskType.WAREHOUSE_DESIGN.value,
    }
)


class RoutingLLMProvider:
    def __init__(
        self,
        *,
        local,
        cloud,
        local_tasks: set[str] | frozenset[str] | tuple[str, ...] = DEFAULT_LOCAL_TASKS,
        fallback_on_error: bool = True,
    ) -> None:
        self.local = local
        self.cloud = cloud
        self.local_tasks = frozenset(local_tasks)
        self.fallback_on_error = fallback_on_error

    def provider_name(self) -> str:
        return "routing"

    def token_count(self, text_or_messages: str | Sequence[LLMMessage]) -> int:
        return self.cloud.token_count(text_or_messages)

    def _select(self, task: str | None):
        if task and task in self.local_tasks:
            return self.local
        return self.cloud

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_type: str | None = None,
    ) -> LLMResponse:
        resolved_task = task_type or current_task()
        target = self._select(resolved_task)
        try:
            return await target.chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                task_type=resolved_task,
            )
        except LLMProviderError as exc:
            if target is self.local and self.fallback_on_error:
                return await self.cloud.chat(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    task_type=resolved_task,
                )
            raise

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_type: str | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        resolved_task = task_type or current_task()
        target = self._select(resolved_task)
        try:
            async for chunk in target.stream_chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                task_type=resolved_task,
            ):
                yield chunk
        except LLMProviderError as exc:
            if target is self.local and self.fallback_on_error:
                async for chunk in self.cloud.stream_chat(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    task_type=resolved_task,
                ):
                    yield chunk
            else:
                raise

    async def health_check(self) -> LLMHealthStatus:
        local_status = await self.local.health_check()
        cloud_status = await self.cloud.health_check()
        return LLMHealthStatus(
            provider="routing",
            ok=local_status.ok and cloud_status.ok,
            api_key_configured=cloud_status.api_key_configured,
            reachable=local_status.reachable or cloud_status.reachable,
            model_available=local_status.model_available or cloud_status.model_available,
            model=f"{self.cloud.provider_name()}/{self.cloud.model if hasattr(self.cloud, 'model') else ''}",
            message=(
                f"local({local_status.provider}: {local_status.message}) | "
                f"cloud({cloud_status.provider}: {cloud_status.message})"
            ),
            latency_ms=max(
                local_status.latency_ms or 0.0,
                cloud_status.latency_ms or 0.0,
            ),
        )

    async def aclose(self) -> None:
        for provider in (self.local, self.cloud):
            closer = getattr(provider, "aclose", None)
            if callable(closer):
                await closer()


__all__ = ["RoutingLLMProvider"]
