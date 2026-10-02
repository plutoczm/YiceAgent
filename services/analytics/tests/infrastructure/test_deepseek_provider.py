import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from backend.app.core.settings import Settings
from backend.app.infrastructure.llm import (
    DeepSeekProvider,
    LLMAuthenticationError,
    LLMConnectionError,
    LLMMessage,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)


pytestmark = pytest.mark.anyio


def make_settings(**deepseek_overrides: Any) -> Settings:
    deepseek = {
        "api_key": "test-api-key",
        "base_url": "https://api.deepseek.com/v1",
        "chat_model": "deepseek-chat",
        "timeout_seconds": 1,
        "max_retries": 1,
    }
    deepseek.update(deepseek_overrides)
    return Settings(_env_file=None, environment="test", deepseek=deepseek)


def json_response(status_code: int, payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status_code=status_code, json=payload)


def success_payload(content: str = "hello") -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "total_tokens": 10,
        },
    }


def make_provider(
    handler: httpx.MockTransport | None = None,
    **deepseek_overrides: Any,
) -> DeepSeekProvider:
    transport = handler or httpx.MockTransport(
        lambda request: json_response(200, success_payload())
    )
    client = httpx.AsyncClient(transport=transport)
    return DeepSeekProvider(settings=make_settings(**deepseek_overrides), client=client)


async def test_provider_name_and_token_count_are_available() -> None:
    provider = make_provider()

    assert provider.provider_name() == "deepseek"
    assert provider.token_count("hello world") == 2
    assert provider.token_count(
        [LLMMessage(role="user", content="hello"), LLMMessage(role="assistant", content="ok")]
    ) == 2

    await provider.aclose()


async def test_chat_success_posts_expected_payload_and_returns_usage() -> None:
    observed_payload: dict[str, Any] = {}
    observed_authorization = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_payload, observed_authorization
        observed_payload = json.loads(request.content.decode("utf-8"))
        observed_authorization = request.headers["authorization"]
        return json_response(200, success_payload("DeepSeek response"))

    provider = make_provider(httpx.MockTransport(handler))

    response = await provider.chat(
        [
            LLMMessage(role="system", content="You are concise."),
            LLMMessage(role="user", content="Say hi"),
        ],
        temperature=0.2,
        max_tokens=128,
    )

    assert observed_authorization == "Bearer test-api-key"
    assert observed_payload["model"] == "deepseek-chat"
    assert observed_payload["messages"][1] == {"role": "user", "content": "Say hi"}
    assert observed_payload["temperature"] == 0.2
    assert observed_payload["max_tokens"] == 128
    assert observed_payload["stream"] is False
    assert response.content == "DeepSeek response"
    assert response.model == "deepseek-chat"
    assert response.usage.total_tokens == 10
    assert response.finish_reason == "stop"

    await provider.aclose()


async def test_stream_chat_yields_chunks_and_usage() -> None:
    stream = SSEStream(
        [
            b'data: {"choices":[{"delta":{"content":"hel"},"finish_reason":null}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n\n',
            b"data: [DONE]\n\n",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}
        return httpx.Response(200, stream=stream)

    provider = make_provider(httpx.MockTransport(handler))

    chunks = [
        chunk
        async for chunk in provider.stream_chat(
            [LLMMessage(role="user", content="stream please")]
        )
    ]

    assert [chunk.content for chunk in chunks] == ["hel", "lo"]
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens == 3

    await provider.aclose()


async def test_health_check_reports_missing_api_key_without_network_call() -> None:
    settings = make_settings(api_key=None)
    transport = httpx.MockTransport(lambda request: pytest.fail("network should not be called"))
    provider = DeepSeekProvider(settings=settings, client=httpx.AsyncClient(transport=transport))

    status = await provider.health_check()

    assert status.provider == "deepseek"
    assert status.ok is False
    assert status.api_key_configured is False
    assert status.reachable is False
    assert status.model_available is False
    assert "API key is not configured" in status.message

    await provider.aclose()


async def test_health_check_validates_reachability_and_model() -> None:
    provider = make_provider()

    status = await provider.health_check()

    assert status.ok is True
    assert status.api_key_configured is True
    assert status.reachable is True
    assert status.model_available is True
    assert status.model == "deepseek-chat"

    await provider.aclose()


async def test_authentication_failure_maps_to_custom_exception() -> None:
    provider = make_provider(
        httpx.MockTransport(
            lambda request: json_response(401, {"error": {"message": "bad key"}})
        )
    )

    with pytest.raises(LLMAuthenticationError, match="bad key"):
        await provider.chat([LLMMessage(role="user", content="hello")])

    await provider.aclose()


async def test_rate_limit_failure_maps_to_custom_exception() -> None:
    provider = make_provider(
        httpx.MockTransport(
            lambda request: json_response(429, {"error": {"message": "slow down"}})
        )
    )

    with pytest.raises(LLMRateLimitError, match="slow down"):
        await provider.chat([LLMMessage(role="user", content="hello")])

    await provider.aclose()


async def test_timeout_maps_to_custom_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    provider = make_provider(httpx.MockTransport(handler), max_retries=0)

    with pytest.raises(LLMTimeoutError, match="timed out"):
        await provider.chat([LLMMessage(role="user", content="hello")])

    await provider.aclose()


async def test_connection_error_retries_then_succeeds() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("connection reset", request=request)
        return json_response(200, success_payload("after retry"))

    provider = make_provider(httpx.MockTransport(handler), max_retries=1)

    response = await provider.chat([LLMMessage(role="user", content="hello")])

    assert attempts == 2
    assert response.content == "after retry"

    await provider.aclose()


async def test_connection_error_after_retries_maps_to_custom_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset", request=request)

    provider = make_provider(httpx.MockTransport(handler), max_retries=1)

    with pytest.raises(LLMConnectionError, match="connection reset"):
        await provider.chat([LLMMessage(role="user", content="hello")])

    await provider.aclose()


async def test_invalid_response_maps_to_provider_error() -> None:
    provider = make_provider(
        httpx.MockTransport(lambda request: json_response(200, {"choices": []}))
    )

    with pytest.raises(LLMProviderError, match="Invalid DeepSeek response"):
        await provider.chat([LLMMessage(role="user", content="hello")])

    await provider.aclose()


class SSEStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
