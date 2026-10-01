import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from backend.app.core.settings import Settings
from backend.app.domain.ports.llm_provider import (
    LLMHealthStatus,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    LLMUsage,
)
from backend.app.infrastructure.llm.exceptions import (
    LLMConnectionError,
    LLMProviderError,
    LLMTimeoutError,
)


class OllamaProvider:
    """实现统一大模型提供方端口的本地 Ollama 适配器。"""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.config = settings.ollama
        self.model = self.config.chat_model
        self.base_url = str(self.config.base_url).rstrip("/")
        self.max_retries = settings.llm.max_retries
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=float(self.config.timeout_seconds),
        )

    def provider_name(self) -> str:
        return "ollama"

    def token_count(self, text_or_messages: str | Sequence[LLMMessage]) -> int:
        if isinstance(text_or_messages, str):
            return max(1, len(text_or_messages) // 4)
        return sum(max(1, len(message.content) // 4) for message in text_or_messages)

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_type: str | None = None,
    ) -> LLMResponse:
        payload = self._payload(messages, temperature, max_tokens, stream=False)
        response = await self._request("POST", "/api/chat", json=payload)
        data = response.json()
        message = data.get("message") or {}
        if not isinstance(message.get("content"), str):
            raise LLMProviderError("Invalid Ollama response")
        return LLMResponse(
            provider=self.provider_name(),
            model=str(data.get("model", self.model)),
            content=message["content"],
            finish_reason="stop" if data.get("done") else None,
            usage=self._usage(data),
        )

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_type: str | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        payload = self._payload(messages, temperature, max_tokens, stream=True)
        try:
            async with self.client.stream("POST", self._url("/api/chat"), json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    content = (data.get("message") or {}).get("content", "")
                    if content or data.get("done"):
                        yield LLMStreamChunk(
                            provider=self.provider_name(),
                            model=str(data.get("model", self.model)),
                            content=content,
                            finish_reason="stop" if data.get("done") else None,
                            usage=self._usage(data) if data.get("done") else None,
                        )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("Ollama request timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMConnectionError(f"Ollama streaming failed: {exc}") from exc

    async def health_check(self) -> LLMHealthStatus:
        started_at = time.perf_counter()
        try:
            response = await self._request("GET", "/api/tags")
            models = [item.get("name", "") for item in response.json().get("models", [])]
            available = any(name == self.model or name.startswith(f"{self.model}:") for name in models)
            return LLMHealthStatus(
                provider=self.provider_name(),
                ok=available,
                api_key_configured=True,
                reachable=True,
                model_available=available,
                model=self.model,
                message="Ollama provider is healthy" if available else f"Model '{self.model}' is not pulled",
                latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
            )
        except LLMProviderError as exc:
            return LLMHealthStatus(
                provider=self.provider_name(),
                ok=False,
                api_key_configured=True,
                reachable=False,
                model_available=False,
                model=self.model,
                message=str(exc),
                latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
            )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def _payload(self, messages, temperature, max_tokens, *, stream):
        options: dict[str, Any] = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        return {
            "model": self.model,
            "messages": [message.model_dump() for message in messages],
            "stream": stream,
            "keep_alive": self.config.keep_alive,
            "options": options,
        }

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        cause: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.request(method, self._url(path), **kwargs)
                response.raise_for_status()
                return response
            except httpx.TimeoutException as exc:
                cause = exc
                error: LLMProviderError = LLMTimeoutError("Ollama request timed out")
            except httpx.HTTPError as exc:
                cause = exc
                error = LLMConnectionError(f"Ollama request failed: {exc}")
            if attempt < self.max_retries:
                await asyncio.sleep(0.2 * (2**attempt))
        raise error from cause

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _usage(self, data: dict[str, Any]) -> LLMUsage:
        prompt = int(data.get("prompt_eval_count", 0) or 0)
        completion = int(data.get("eval_count", 0) or 0)
        return LLMUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
        )
