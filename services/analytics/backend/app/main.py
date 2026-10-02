import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.app.application.agent.exceptions import AgentError
from backend.app.core.constants import APP_VERSION
from backend.app.core.config import get_settings
from backend.app.core.logging_config import setup_fastapi_logging
from backend.app.core.logger import get_request_id
from backend.app.infrastructure.llm.exceptions import LLMProviderError
from backend.app.infrastructure.embeddings.exceptions import EmbeddingProviderError
from backend.app.infrastructure.vectorstore.exceptions import VectorStoreError
from backend.app.presentation.api.router import api_router
from backend.app.presentation.api.schemas.common import ErrorDetail, ErrorResponse


def create_app() -> FastAPI:
    settings = get_settings()
    logger = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.identity.enabled and settings.business_analytics.enabled:
            from backend.app.presentation.api.dependencies.business_analytics import (
                get_trusted_analytics_runtime,
            )

            get_trusted_analytics_runtime()
        if logger is not None:
            logger.info(
                "Application startup",
                extra={
                    "environment": settings.environment.value,
                    "api_only": settings.runtime.api_only,
                    "gpu_enabled": settings.runtime.gpu_enabled,
                },
            )
        yield
        if logger is not None:
            logger.info("Application shutdown")

    app = FastAPI(
        title=settings.app.name,
        description="AI 数据工程智能助手平台 API",
        version=APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "Health", "description": "应用及依赖健康状态。"},
            {"name": "Config", "description": "运行时能力查询。"},
            {"name": "Knowledge Base", "description": "文档摄取与 RAG 查询接口。"},
            {"name": "Chat", "description": "流式对话接口。"},
            {"name": "Text2SQL", "description": "自然语言生成 SQL 接口。"},
            {"name": "SQL Review", "description": "SQL 质量、风险与优化审核接口。"},
            {"name": "Warehouse Design", "description": "分层数仓设计生成接口。"},
            {"name": "Agent", "description": "统一 LangGraph 智能体路由接口。"},
            {"name": "Business Analytics", "description": "认证后的受管企业数据分析。"},
        ],
    )
    logger = setup_fastapi_logging(app, settings)

    def _request_id(request: Request) -> str | None:
        return getattr(request.state, "request_id", None) or get_request_id()

    def _error_headers(request: Request) -> dict[str, str]:
        request_id = _request_id(request)
        trace_id = getattr(request.state, "trace_id", None)
        headers: dict[str, str] = {}
        if request_id:
            headers[settings.logging.request_id_header] = request_id
        if trace_id:
            headers[settings.logging.trace_id_header] = trace_id
        return headers

    @app.middleware("http")
    async def request_timing(request: Request, call_next):
        started_at = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
        response.headers["x-process-time-ms"] = str(duration_ms)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="validation_error",
                    message="请求参数校验失败",
                    details=[
                        {
                            key: error[key]
                            for key in ("type", "loc", "msg")
                            if key in error
                        }
                        for error in exc.errors()
                    ],
                ),
                request_id=_request_id(request),
            ).model_dump(mode="json"),
            headers=_error_headers(request),
        )

    @app.exception_handler(LLMProviderError)
    async def llm_exception_handler(request: Request, exc: LLMProviderError) -> JSONResponse:
        logger.error("LLM provider error", extra={"error": str(exc)})
        return JSONResponse(
            status_code=502,
            content=ErrorResponse(
                error=ErrorDetail(code="llm_provider_error", message=str(exc)),
                request_id=_request_id(request),
            ).model_dump(mode="json"),
            headers=_error_headers(request),
        )

    @app.exception_handler(VectorStoreError)
    async def vector_exception_handler(request: Request, exc: VectorStoreError) -> JSONResponse:
        logger.error("Vector store error", extra={"error": str(exc)})
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error=ErrorDetail(code="vector_store_error", message=str(exc)),
                request_id=_request_id(request),
            ).model_dump(mode="json"),
            headers=_error_headers(request),
        )

    @app.exception_handler(EmbeddingProviderError)
    async def embedding_exception_handler(
        request: Request,
        exc: EmbeddingProviderError,
    ) -> JSONResponse:
        logger.error("Embedding provider error", extra={"error": str(exc)})
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="embedding_provider_error",
                    message=str(exc),
                ),
                request_id=_request_id(request),
            ).model_dump(mode="json"),
            headers=_error_headers(request),
        )

    @app.exception_handler(AgentError)
    async def agent_exception_handler(request: Request, exc: AgentError) -> JSONResponse:
        logger.error("Agent error", extra={"error": str(exc)})
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error=ErrorDetail(code="agent_error", message=str(exc)),
                request_id=_request_id(request),
            ).model_dump(mode="json"),
            headers=_error_headers(request),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API exception")
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="internal_server_error",
                    message="服务器内部错误",
                ),
                request_id=_request_id(request),
            ).model_dump(mode="json"),
            headers=_error_headers(request),
        )

    app.include_router(api_router)
    return app


app = create_app()
