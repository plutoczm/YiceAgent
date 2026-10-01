from fastapi import APIRouter, Depends

from backend.app.core.settings import Settings
from backend.app.presentation.api.dependencies.providers import get_app_settings
from backend.app.presentation.api.schemas.knowledge import RuntimeConfigResponse


router = APIRouter(prefix="/api/v1/config", tags=["Config"])


@router.get(
    "/runtime",
    response_model=RuntimeConfigResponse,
    summary="运行时能力",
    description="返回不含敏感信息的运行能力和提供方选择。",
)
def runtime_config(settings: Settings = Depends(get_app_settings)) -> RuntimeConfigResponse:
    return RuntimeConfigResponse(
        environment=settings.environment.value,
        api_only=settings.runtime.api_only,
        gpu_enabled=settings.runtime.gpu_enabled,
        cpu_only=settings.runtime.cpu_only,
        default_llm_provider=settings.llm.default_provider.value,
        embedding_model=settings.embeddings.default_model,
        vector_store="chromadb",
        capabilities={
            "rag": True,
            "streaming": settings.llm.streaming_enabled,
            "document_upload": True,
            "gpu_optional": True,
            "bge_m3": settings.embeddings.default_model == "BAAI/bge-m3",
            "multi_instance_memory": settings.memory.backend == "redis",
            "long_term_memory": True,
            "rule_memory": True,
        },
    )
