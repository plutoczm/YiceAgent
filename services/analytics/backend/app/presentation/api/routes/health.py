import asyncio

from fastapi import APIRouter, Depends

from backend.app.core.constants import APP_VERSION
from backend.app.core.settings import Settings
from backend.app.domain.ports.llm_provider import LLMHealthStatus, LLMProvider
from backend.app.domain.ports.vector_store import VectorStore
from backend.app.infrastructure.vectorstore.exceptions import CollectionNotFoundError
from backend.app.presentation.api.dependencies.providers import (
    DEFAULT_KNOWLEDGE_COLLECTION,
    get_app_settings,
    get_llm_provider,
    get_vector_store,
)
from backend.app.presentation.api.schemas.health import (
    HealthResponse,
    LivenessResponse,
    ProviderHealthResponse,
    RootResponse,
    VectorStoreHealthResponse,
)


router = APIRouter(tags=["Health"])


@router.get(
    "/",
    response_model=RootResponse,
    summary="服务元数据",
    description="返回服务元数据和 API 文档链接。",
)
def root(settings: Settings = Depends(get_app_settings)) -> RootResponse:
    return RootResponse(
        service=settings.app.name,
        version=APP_VERSION,
        status="running",
        docs="/docs",
        openapi="/openapi.json",
    )


@router.get(
    "/health/live",
    response_model=LivenessResponse,
    summary="进程存活状态",
    description="仅检查 API 进程是否可响应，不访问外部依赖。",
)
def liveness(settings: Settings = Depends(get_app_settings)) -> LivenessResponse:
    return LivenessResponse(service=settings.app.name, status="ok")


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="服务健康状态",
    description="检查应用、大模型提供方和向量库状态。",
)
async def health(
    settings: Settings = Depends(get_app_settings),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    vector_store: VectorStore = Depends(get_vector_store),
) -> HealthResponse:
    try:
        llm_status = await asyncio.wait_for(
            llm_provider.health_check(),
            timeout=settings.health.dependency_timeout_seconds,
        )
    except TimeoutError:
        llm_status = LLMHealthStatus(
            provider=llm_provider.provider_name(),
            ok=False,
            api_key_configured=False,
            reachable=False,
            model_available=False,
            message=(
                "LLM health check exceeded "
                f"{settings.health.dependency_timeout_seconds:g}s timeout"
            ),
        )
    try:
        stats = vector_store.collection_stats(DEFAULT_KNOWLEDGE_COLLECTION)
        vector_status = "ok"
        document_count = stats.document_count
    except CollectionNotFoundError:
        vector_status = "missing"
        document_count = 0
    return HealthResponse(
        service=settings.app.name,
        status="ok" if llm_status.ok and vector_status == "ok" else "degraded",
        environment=settings.environment.value,
        llm_provider=ProviderHealthResponse(**llm_status.model_dump(exclude={"latency_ms"})),
        vector_store=VectorStoreHealthResponse(
            status=vector_status,
            collection_name=DEFAULT_KNOWLEDGE_COLLECTION,
            document_count=document_count,
        ),
    )
