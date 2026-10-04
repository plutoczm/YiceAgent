"""Local-only v2 application; domain writes require explicit human commands."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from workflowops.business.rules import BusinessError
from workflowops.business.service import BusinessService, UnknownOutcome
from workflowops.identity import Sessions

STATIC = Path(__file__).with_name("static")


def create_app(business: BusinessService | None = None) -> FastAPI:
    app = FastAPI(title="WorkflowOps v2", docs_url=None, redoc_url=None)

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
            "phase": "business" if business else "foundation",
            "live_verified": False,
        }

    @app.get("/api/v2/models")
    def models():
        return {"channels": ["chatgpt", "deepseek", "qwen"], "readiness": "NOT_RUN"}

    if business is not None:
        from workflowops.business.api import router

        app.include_router(router(business, Sessions()))
        app.mount("/static", StaticFiles(directory=STATIC), name="static")

        @app.get("/")
        def console():
            return FileResponse(STATIC / "index.html")

    return app
