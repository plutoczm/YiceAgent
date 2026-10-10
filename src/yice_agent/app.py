"""Local-only v2 application; domain writes require explicit human commands."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from yice_agent.business.rules import BusinessError
from yice_agent.business.service import BusinessService, UnknownOutcome
from yice_agent.identity import Sessions

STATIC = Path(__file__).with_name("static")


def create_app(business: BusinessService | None = None, models=None, agent=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if agent is not None:
                await agent.close()
            if models is not None:
                await models.close()

    app = FastAPI(title="忆策 Agent", docs_url=None, redoc_url=None, lifespan=lifespan)

    from yice_agent.model_store import ModelError

    @app.exception_handler(ModelError)
    async def model_error(request: Request, error: ModelError):
        import re

        code = str(error)
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) is None:
            code = "model_operation_failed"
        status = (
            409
            if code
            in {
                "paid_channel_not_enabled",
                "chatgpt_model_required",
                "knowledge_not_indexed",
                "oauth_login_required",
            }
            else 504
            if code in {"provider_timeout", "local_compute_busy"}
            else 502
        )
        return JSONResponse({"error": code}, status_code=status)

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0]
        origin = request.headers.get("origin")
        if (
            host not in {"127.0.0.1", "localhost", "testserver"}
            or origin is not None
            and origin not in {"http://127.0.0.1:8020", "http://localhost:8020"}
        ):
            return JSONResponse({"error": "local_origin_required"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(BusinessError)
    async def business_error(request, error):
        result = {
            "error": error.code,
            "status": "unknown_outcome" if isinstance(error, UnknownOutcome) else "failed",
        }
        if isinstance(error, UnknownOutcome):
            result["domain_refs"] = error.references
        return JSONResponse(result, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse({"error": "invalid_request", "status": "failed"}, status_code=422)

    @app.get("/health")
    def health():
        return {
            "owner": "workflowops-v2",
            "status": "ok",
            "phase": "workbench"
            if agent is not None
            else "models-rag"
            if models is not None
            else "business"
            if business
            else "foundation",
            "live_verified": False,
        }

    if models is None:

        @app.get("/api/v2/models")
        def model_placeholder():
            return {"channels": ["chatgpt", "deepseek", "qwen"], "readiness": "NOT_RUN"}

    if business is not None:
        from yice_agent.business.api import router

        sessions = Sessions()
        app.include_router(router(business, sessions))
        if models is not None:
            from yice_agent.models.api import router as model_router

            app.include_router(model_router(models, sessions))

        if agent is not None:
            from yice_agent.agent.api import router as agent_router

            app.include_router(agent_router(agent, sessions))

        @app.get("/agent")
        def agent_console():
            return FileResponse(STATIC / "index.html")

        app.mount("/static", StaticFiles(directory=STATIC), name="static")

        @app.get("/")
        def console():
            return FileResponse(STATIC / "index.html")

    return app
