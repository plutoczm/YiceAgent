"""Minimal offline factory; never instantiate the reference Agent runtime."""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.adapters.supportops import SupportOpsAdapter
from app.api.workflows import router
from app.demo.identity import Sessions
from app.runs.store import RunStore
from app.workflows.engine import WorkflowEngine

ROOT = Path(__file__).resolve().parents[6]


def demo_app(adapter: SupportOpsAdapter, run_root: Path) -> FastAPI:
    """Build only the P3 graph, sessions and run store."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await adapter.close()

    app = FastAPI(title="WorkflowOps offline demo", lifespan=lifespan)
    app.state.sessions = Sessions()
    app.state.engine = WorkflowEngine(adapter, RunStore(run_root))

    @app.middleware("http")
    async def local_only(request: Request, call_next: Any) -> Response:
        host = request.headers.get("host", "").split(":")[0]
        origin = request.headers.get("origin")
        if host not in {"127.0.0.1", "localhost", "testserver"} or (
            origin is not None and origin not in {"http://127.0.0.1:8010", "http://localhost:8010"}
        ):
            return JSONResponse({"detail": "local_demo_only"}, status_code=403)
        response: Response = await call_next(request)
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "offline", "service": "workflowops-harness"}

    app.include_router(router)
    return app


def create_app() -> FastAPI:
    """Read only the launcher-owned ignored demo configuration."""
    path = Path(os.environ["WORKFLOWOPS_DEMO_CONFIG"]).resolve()
    if not path.is_relative_to(ROOT / ".runtime"):
        raise ValueError("demo_config_outside_runtime")
    config = json.loads(path.read_text(encoding="utf-8"))
    if config["owner"] != "workflowops-demo":
        raise ValueError("unowned_demo_config")
    return demo_app(SupportOpsAdapter(config["supportops_tokens"]), ROOT / ".runtime/runs")
