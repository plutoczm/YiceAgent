import pytest

from backend.app.domain.ports.llm_provider import (
    LLMHealthStatus,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    TaskType,
    llm_task,
)
from backend.app.infrastructure.llm import (
    LLMProviderError,
    RoutingLLMProvider,
    TaskBoundLLMProvider,
)


pytestmark = pytest.mark.anyio


class FakeProvider:
    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.calls: list[dict] = []

    def provider_name(self) -> str:
        return self.name

    def token_count(self, text_or_messages) -> int:
        if isinstance(text_or_messages, str):
            return len(text_or_messages.split())
        return len(text_or_messages)

    async def chat(self, messages, *, temperature=None, max_tokens=None, task_type=None):
        self.calls.append(
            {
                "temperature": temperature,
                "max_tokens": max_tokens,
                "task_type": task_type,
            }
        )
        if self.fail:
            raise LLMProviderError(f"{self.name} failed", provider=self.name)
        return LLMResponse(
            provider=self.name,
            model=self.name,
            content=f"from {self.name}",
        )

    async def stream_chat(self, messages, *, temperature=None, max_tokens=None, task_type=None):
        self.calls.append({"task_type": task_type})
        if self.fail:
            raise LLMProviderError(f"{self.name} failed", provider=self.name)
        yield LLMStreamChunk(provider=self.name, model=self.name, content="hi")

    async def health_check(self) -> LLMHealthStatus:
        return LLMHealthStatus(
            provider=self.name,
            ok=not self.fail,
            api_key_configured=True,
            reachable=not self.fail,
            model_available=not self.fail,
            model=self.name,
            message=f"{self.name} healthy" if not self.fail else f"{self.name} down",
            latency_ms=1.0,
        )


def make_router(*, local=None, cloud=None, local_tasks=None, fallback=True) -> RoutingLLMProvider:
    return RoutingLLMProvider(
        local=local or FakeProvider("local"),
        cloud=cloud or FakeProvider("cloud"),
        local_tasks=local_tasks or {"text2sql"},
        fallback_on_error=fallback,
    )


async def test_provider_name_and_token_count() -> None:
    router = make_router()

    assert router.provider_name() == "routing"
    assert router.token_count("hello world") == 2
    assert router.token_count([LLMMessage(role="user", content="hi")]) == 1


async def test_routing_dispatches_by_context_task() -> None:
    local = FakeProvider("local")
    cloud = FakeProvider("cloud")
    router = make_router(local=local, cloud=cloud, local_tasks={"text2sql"})

    with llm_task(TaskType.TEXT2SQL):
        local_response = await router.chat([LLMMessage(role="user", content="gen sql")])
    with llm_task(TaskType.GENERAL_CHAT):
        cloud_response = await router.chat([LLMMessage(role="user", content="hello")])

    assert local_response.provider == "local"
    assert cloud_response.provider == "cloud"
    assert local.calls[0]["task_type"] == "text2sql"
    assert cloud.calls[0]["task_type"] == "general_chat"


async def test_routing_dispatches_by_explicit_task_type_kwarg() -> None:
    local = FakeProvider("local")
    cloud = FakeProvider("cloud")
    router = make_router(local=local, cloud=cloud, local_tasks={"warehouse_design"})

    local_response = await router.chat(
        [LLMMessage(role="user", content="design")],
        task_type="warehouse_design",
    )
    cloud_response = await router.chat(
        [LLMMessage(role="user", content="hi")],
        task_type="rag",
    )

    assert local_response.provider == "local"
    assert cloud_response.provider == "cloud"


async def test_unknown_task_falls_back_to_cloud() -> None:
    router = make_router(local_tasks={"text2sql"})

    response = await router.chat(
        [LLMMessage(role="user", content="hello")],
        task_type="general_chat",
    )

    assert response.provider == "cloud"


async def test_local_failure_falls_back_to_cloud() -> None:
    router = make_router(
        local=FakeProvider("local", fail=True),
        cloud=FakeProvider("cloud"),
        local_tasks={"text2sql"},
        fallback=True,
    )

    response = await router.chat(
        [LLMMessage(role="user", content="gen sql")],
        task_type="text2sql",
    )

    assert response.provider == "cloud"


async def test_no_fallback_when_disabled_raises() -> None:
    router = make_router(
        local=FakeProvider("local", fail=True),
        cloud=FakeProvider("cloud"),
        local_tasks={"text2sql"},
        fallback=False,
    )

    with pytest.raises(LLMProviderError, match="local failed"):
        await router.chat(
            [LLMMessage(role="user", content="gen sql")],
            task_type="text2sql",
        )


async def test_cloud_failure_never_falls_back() -> None:
    router = make_router(
        local=FakeProvider("local"),
        cloud=FakeProvider("cloud", fail=True),
        local_tasks={"text2sql"},
        fallback=True,
    )

    with pytest.raises(LLMProviderError, match="cloud failed"):
        await router.chat(
            [LLMMessage(role="user", content="hello")],
            task_type="general_chat",
        )


async def test_stream_chat_routes_by_task() -> None:
    router = make_router(local_tasks={"text2sql"})

    chunks = [
        chunk
        async for chunk in router.stream_chat(
            [LLMMessage(role="user", content="gen sql")],
            task_type="text2sql",
        )
    ]

    assert len(chunks) == 1
    assert chunks[0].provider == "local"
    assert chunks[0].content == "hi"


async def test_health_check_aggregates_both_providers() -> None:
    router = make_router(
        local=FakeProvider("local", fail=True),
        cloud=FakeProvider("cloud"),
    )

    status = await router.health_check()

    assert status.provider == "routing"
    assert status.ok is False
    assert "local" in status.message
    assert "cloud" in status.message


async def test_task_bound_provider_injects_context() -> None:
    local = FakeProvider("local")
    router = make_router(local=local, cloud=FakeProvider("cloud"), local_tasks={"text2sql"})
    bound = TaskBoundLLMProvider(router, TaskType.TEXT2SQL)

    response = await bound.chat([LLMMessage(role="user", content="gen sql")])

    assert response.provider == "local"
    assert local.calls[0]["task_type"] == "text2sql"


async def test_task_bound_provider_delegates_health_and_name() -> None:
    cloud = FakeProvider("cloud")
    router = make_router(local=FakeProvider("local"), cloud=cloud)
    bound = TaskBoundLLMProvider(router, TaskType.RAG)

    assert bound.provider_name() == "routing"
    status = await bound.health_check()

    assert status.provider == "routing"
    assert status.ok is True
