import json

import httpx
import pytest

from backend.app.core.settings import Settings
from backend.app.domain.ports.llm_provider import LLMMessage
from backend.app.infrastructure.llm import OllamaProvider


pytestmark = pytest.mark.anyio


def make_provider(handler) -> OllamaProvider:
    settings = Settings(
        _env_file=None,
        environment="test",
        llm={"default_provider": "ollama", "max_retries": 0},
        ollama={
            "enabled": True,
            "base_url": "http://ollama:11434",
            "chat_model": "qwen3",
            "timeout_seconds": 5,
        },
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaProvider(settings, client=client)


async def test_ollama_chat_maps_payload_and_usage() -> None:
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "model": "qwen3",
                "message": {"role": "assistant", "content": "local answer"},
                "done": True,
                "prompt_eval_count": 8,
                "eval_count": 3,
            },
        )

    provider = make_provider(handler)
    response = await provider.chat(
        [LLMMessage(role="user", content="hello")], temperature=0.0, max_tokens=64
    )

    assert observed["model"] == "qwen3"
    assert observed["options"] == {"temperature": 0.0, "num_predict": 64}
    assert response.content == "local answer"
    assert response.usage.total_tokens == 11
    await provider.aclose()


async def test_ollama_health_checks_model_availability() -> None:
    provider = make_provider(
        lambda request: httpx.Response(200, json={"models": [{"name": "qwen3:latest"}]})
    )

    status = await provider.health_check()

    assert status.ok is True
    assert status.model_available is True
    await provider.aclose()
