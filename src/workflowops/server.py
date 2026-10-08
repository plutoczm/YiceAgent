"""Owned local server entrypoint. No automatic schema or data mutation."""

import os

import uvicorn

from workflowops.app import create_app
from workflowops.business.bootstrap import ready
from workflowops.business.service import BusinessService
from workflowops.config import Settings
from workflowops.database import make_engine
from workflowops.models.api import Services


def main():
    marker = os.environ.get("WORKFLOWOPS_V2_STARTUP_MARKER")
    if not marker or len(marker) != 32:
        raise RuntimeError("owned_startup_marker_required")
    schema = os.environ.get("WORKFLOWOPS_V2_TEST_SCHEMA", "public")
    engine = make_engine(Settings.load(), schema)
    if not ready(engine):
        engine.dispose()
        raise RuntimeError("business_database_not_prepared")
    from workflowops.agent.runtime import AgentService

    business = BusinessService(engine)
    resources = Services.create(engine, Settings.load())
    agent = AgentService(business, resources)
    agent.store.recover()
    app = create_app(business, models=resources, agent=agent)

    @app.middleware("http")
    async def startup_marker(request, call_next):
        response = await call_next(request)
        response.headers["X-WorkflowOps-V2-Startup"] = marker
        return response

    uvicorn.run(app, host="127.0.0.1", port=8020, workers=1, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
