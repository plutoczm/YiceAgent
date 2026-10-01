"""Child service runner: fixed imports, loopback binding and startup marker."""

import importlib
import json
import os
import sys
from pathlib import Path

import uvicorn
from demo_process import process_identity

ROOT = Path(__file__).resolve().parents[1]


def main():
    service = sys.argv[1]
    if service not in {"harness", "supportops"}:
        raise ValueError("unsupported demo service")
    path = Path(os.environ["WORKFLOWOPS_DEMO_CONFIG"]).resolve()
    if not path.is_relative_to(ROOT / ".runtime"):
        raise ValueError("invalid demo config")
    config = json.loads(path.read_text(encoding="utf-8"))
    module = importlib.import_module("app.demo.composition")
    if not Path(module.__file__).resolve().is_relative_to(ROOT / "services" / service):
        raise ValueError("import escaped service snapshot")
    app = module.create_app()

    @app.middleware("http")
    async def startup_marker(request, call_next):
        response = await call_next(request)
        response.headers["X-WorkflowOps-Startup"] = config["marker"]
        response.headers["X-WorkflowOps-Service"] = service
        return response

    record = process_identity(os.getpid())
    record.update(
        owner="workflowops-demo",
        service=service,
        marker=config["marker"],
        config=str(path),
    )
    marker = path.parent / (service + "-process.json")
    with marker.open("x", encoding="utf-8") as stream:
        json.dump(record, stream)
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=8010 if service == "harness" else 8011,
            access_log=False,
            log_level="critical",
            workers=1,
        )
    finally:
        marker.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
