"""通用 OpenAI 兼容 Chat Completions provider。

同一份实现同时服务两类端点：
- OpenAI 云端（https://api.openai.com/v1，需 Bearer 鉴权）
- Ollama 本地 OpenAI 兼容接口（http://127.0.0.1:11434/v1，无需鉴权）

构造器接收 APIProviderSettings 兼容的配置对象（base_url/chat_model/timeout_seconds/
max_retries/api_key），因此 settings.openai 与 settings.local 均可直接复用。
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
from pydantic import ValidationError

from backend.app.domain.ports.llm_provider import (
    LLMHealthStatus,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    LLMUsage,
)
from backend.app.infrastructure.llm.exceptions import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)


class OpenAIProvider:
    _provider_name = "openai"

    def __init__(
        self,
        config: Any,
        *,
        provider_name: str = "openai",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._provider_name = provider_name
        self.model = config.chat_model
        self.base_url = str(config.base_url).rstrip("/")
        self.max_retries = config.max_retries
        self.timeout = httpx.Timeout(
            timeout=float(config.timeout_seconds),
            connect=min(10.0, float(config.timeout_seconds)),
            read=float(config.timeout_seconds),
        )
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
        )

    def provider_name(self) -> str:
        return self._provider_name

    def token_count(self, text_or_messages: str | Sequence[LLMMessage]) -> int:
        if isinstance(text_or_messages, str):
            return len(text_or_messages.split())
        return sum(len(message.content.split()) for message in text_or_messages)

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_type: str | None = None,
    ) -> LLMResponse:
        payload = self._build_payload(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        response_payload = await self._request_json(payload)
        return self._parse_chat_response(response_payload)

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_type: str | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        payload = self._build_payload(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        payload["stream_options"] = {"include_usage": True}
        async for chunk in self._request_stream(payload):
            yield chunk

    async def health_check(self) -> LLMHealthStatus:
        started_at = time.perf_counter()
        api_key_configured = self._api_key() is not None
        try:
            await self.chat([LLMMessage(role="user", content="health check")], max_tokens=1)
        except LLMProviderError as exc:
            return LLMHealthStatus(
                provider=self.provider_name(),
                ok=False,
                api_key_configured=api_key_configured,
                reachable=not isinstance(exc, (LLMConnectionError, LLMTimeoutError)),
                model_available=False,
                model=self.model,
                message=str(exc),
                latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
            )

        return LLMHealthStatus(
            provider=self.provider_name(),
            ok=True,
            api_key_configured=api_key_configured,
            reachable=True,
            model_available=True,
            model=self.model,
            message=f"{self.provider_name()} provider is healthy",
            latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def _build_payload(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.model_dump() for message in messages],
            "stream": stream,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    async def _request_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._send_with_retries(payload)
        try:
            response_payload = response.json()
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                f"Invalid {self.provider_name()} response: response body is not valid JSON",
                provider=self.provider_name(),
                status_code=response.status_code,
            ) from exc
        self._raise_for_error_payload(response_payload, response.status_code)
        return response_payload

    async def _request_stream(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[LLMStreamChunk]:
        response = await self._send_with_retries(payload)
        async for line in response.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError as exc:
                raise LLMProviderError(
                    f"Invalid {self.provider_name()} stream response: event is not valid JSON",
                    provider=self.provider_name(),
                    status_code=response.status_code,
                ) from exc
            yield self._parse_stream_event(event)

    async def _send_with_retries(self, payload: dict[str, Any]) -> httpx.Response:
        last_error: LLMProviderError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.post(
                    self._endpoint_path(),
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code >= 400:
                    self._raise_for_http_status(response)
                return response
            except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                last_error = LLMTimeoutError(
                    str(exc),
                    provider=self.provider_name(),
                    retryable=True,
                )
            except httpx.ConnectError as exc:
                last_error = LLMConnectionError(
                    str(exc),
                    provider=self.provider_name(),
                    retryable=True,
                )
            except LLMProviderError as exc:
                if not exc.retryable:
                    raise
                last_error = exc

            if attempt < self.max_retries:
                await asyncio.sleep(0.1 * (2**attempt))

        if last_error is not None:
            raise last_error
        raise LLMProviderError(
            f"{self.provider_name()} request failed",
            provider=self.provider_name(),
        )

    def _endpoint_path(self) -> str:
        if self.client.base_url == httpx.URL(""):
            return f"{self.base_url}/chat/completions"
        return "/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        api_key = self._api_key()
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        return headers

    def _api_key(self) -> str | None:
        if self.config.api_key is None:
            return None
        value = self.config.api_key.get_secret_value().strip()
        return value or None

    def _raise_for_http_status(self, response: httpx.Response) -> None:
        message = self._extract_error_message(response)
        if response.status_code in (401, 403):
            raise LLMAuthenticationError(
                message,
                provider=self.provider_name(),
                status_code=response.status_code,
            )
        if response.status_code == 429:
            raise LLMRateLimitError(
                message,
                provider=self.provider_name(),
                status_code=response.status_code,
                retryable=False,
            )
        if 500 <= response.status_code < 600:
            raise LLMProviderError(
                message,
                provider=self.provider_name(),
                status_code=response.status_code,
                retryable=True,
            )
        raise LLMProviderError(
            message,
            provider=self.provider_name(),
            status_code=response.status_code,
        )

    def _extract_error_message(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return f"{self.provider_name()} HTTP {response.status_code}"
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        if isinstance(error, str) and error:
            return error
        return f"{self.provider_name()} HTTP {response.status_code}"

    def _raise_for_error_payload(
        self,
        payload: dict[str, Any],
        status_code: int,
    ) -> None:
        if "error" not in payload:
            return
        fake_response = httpx.Response(status_code=status_code, json=payload)
        self._raise_for_http_status(fake_response)

    def _parse_chat_response(self, payload: dict[str, Any]) -> LLMResponse:
        try:
            choices = payload["choices"]
            first_choice = choices[0]
            message = first_choice["message"]
            usage_payload = payload.get("usage", {})
            return LLMResponse(
                provider=self.provider_name(),
                model=payload.get("model", self.model),
                content=message["content"],
                finish_reason=first_choice.get("finish_reason"),
                usage=LLMUsage(
                    prompt_tokens=usage_payload.get("prompt_tokens", 0),
                    completion_tokens=usage_payload.get("completion_tokens", 0),
                    total_tokens=usage_payload.get("total_tokens", 0),
                ),
                raw_response_id=payload.get("id"),
            )
        except (KeyError, IndexError, TypeError, ValidationError) as exc:
            raise LLMProviderError(
                f"Invalid {self.provider_name()} response",
                provider=self.provider_name(),
            ) from exc

    def _parse_stream_event(self, payload: dict[str, Any]) -> LLMStreamChunk:
        try:
            first_choice = payload["choices"][0]
            delta = first_choice.get("delta", {})
            usage = None
            if payload.get("usage") is not None:
                usage_payload = payload["usage"]
                usage = LLMUsage(
                    prompt_tokens=usage_payload.get("prompt_tokens", 0),
                    completion_tokens=usage_payload.get("completion_tokens", 0),
                    total_tokens=usage_payload.get("total_tokens", 0),
                )
            return LLMStreamChunk(
                provider=self.provider_name(),
                model=payload.get("model", self.model),
                content=delta.get("content", ""),
                finish_reason=first_choice.get("finish_reason"),
                usage=usage,
            )
        except (KeyError, IndexError, TypeError, ValidationError) as exc:
            raise LLMProviderError(
                f"Invalid {self.provider_name()} stream response",
                provider=self.provider_name(),
            ) from exc
