"""Explicit offline-only factory; production JWT composition stays unchanged."""

import json
import os
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from backend.app.application.business_analytics.errors import BusinessAnalyticsError
from backend.app.application.business_analytics.governed_workflow import (
    GovernedBusinessAnalyticsWorkflow,
)
from backend.app.application.business_analytics.managed_models import (
    ManagedAnalyticsPolicy,
)
from backend.app.application.business_analytics.managed_text2sql import (
    ManagedText2SQLPipeline,
)
from backend.app.application.business_analytics.workflow import (
    BusinessAnalyticsWorkflow,
)
from backend.app.application.demo_reports.service import (
    ReportRequest,
    ReportView,
    TemplateGenerator,
    execute_report,
)
from backend.app.application.identity.context_factory import (
    BusinessAnalyticsContextFactory,
)
from backend.app.application.identity.models import AuthenticatedPrincipal
from backend.app.infrastructure.business_analytics.delivery_v2 import (
    BusinessDataDeliveryV2Consumer,
)
from backend.app.infrastructure.business_analytics.duckdb_executor import (
    DuckDBBusinessAnalyticsExecutor,
)
from backend.app.infrastructure.business_data.contract_catalog import (
    BusinessDataContractCatalog,
)
from backend.app.infrastructure.demo_delivery.resolver import PublishedDeliveryResolver
from backend.app.infrastructure.identity.configured_tenant_access import (
    ConfiguredTenantAccessResolver,
    TenantGrantConfiguration,
)
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException

ROOT = Path(__file__).resolve().parents[5]
SERVICE_ROOT = ROOT / "services/datacopilot"
PERSONAS = {"customer-a1", "customer-a2", "operator-a", "customer-b1"}
ISSUER = "workflowops-local-demo"


class FileAudit:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def emit(self, event):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json() + "\n")


class DemoReports:
    def __init__(self, root: Path, audit):
        self.catalog = BusinessDataContractCatalog.from_repository_root(SERVICE_ROOT)
        contract = self.catalog.load_accepted_contract().contract
        self.grants = ConfiguredTenantAccessResolver(
            grants=[
                TenantGrantConfiguration(
                    issuer=ISSUER,
                    subject="operator-a",
                    tenant_id="tenant-a",
                    contract_name=contract.contract_name,
                    contract_version=contract.version,
                    allowed_datasets=tuple(
                        dataset.name for dataset in contract.datasets
                    ),
                )
            ],
            accepted_contract_name=contract.contract_name,
            accepted_contract_version=contract.version,
            accepted_dataset_names=tuple(dataset.name for dataset in contract.datasets),
        )
        self.context_factory = BusinessAnalyticsContextFactory()
        self.resolver = PublishedDeliveryResolver(root)
        self.audit = audit

    async def run(self, report_id, principal, *, clock=None):
        grant = self.grants.resolve_access(principal)
        context = self.context_factory.create_context(
            principal=principal, grant=grant, request_id=str(uuid4()), entrypoint="api"
        )
        as_of = (clock or (lambda: datetime.now(UTC)))()
        # Resolve once per request; every validation/materialization uses this exact directory.
        try:
            pinned = self.resolver.pin(context.tenant_id)
        except BusinessAnalyticsError as error:
            from backend.app.application.business_analytics.governed_models import (
                BusinessAnalyticsAuditEvent,
            )

            self.audit.emit(
                BusinessAnalyticsAuditEvent(
                    request_id=context.request_id,
                    tenant_id=context.tenant_id,
                    authenticated_subject_fingerprint=context.authenticated_subject_fingerprint,
                    authorization_grant_id=context.authorization_grant_id,
                    entrypoint="api",
                    contract_name=context.contract_name,
                    contract_version=context.contract_version,
                    status="failed",
                    error_code=error.code,
                )
            )
            raise
        policy = ManagedAnalyticsPolicy()
        consumer = BusinessDataDeliveryV2Consumer(
            delivery_resolver=pinned, contract_catalog=self.catalog
        )
        workflow = GovernedBusinessAnalyticsWorkflow(
            text2sql_pipeline=ManagedText2SQLPipeline(
                workflow=BusinessAnalyticsWorkflow(catalog_port=self.catalog),
                generator=TemplateGenerator(report_id, as_of),
                policy=policy,
            ),
            execution_port=DuckDBBusinessAnalyticsExecutor(
                delivery_consumer=consumer,
                catalog_port=self.catalog,
                managed_policy=policy,
            ),
            audit_sink=self.audit,
        )
        return await execute_report(workflow, report_id, context, as_of)


def demo_app(tokens, root: Path, audit) -> FastAPI:
    if (
        set(tokens) != PERSONAS
        or len(set(tokens.values())) != len(PERSONAS)
        or any(len(v) < 32 for v in tokens.values())
    ):
        raise ValueError("invalid_demo_tokens")
    reports = DemoReports(root, audit)
    app = FastAPI(title="WorkflowOps offline template reports")
    router = APIRouter()

    def principal(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "bearer_token_required")
        token = authorization.removeprefix("Bearer ")
        for name, expected in tokens.items():
            if secrets.compare_digest(token, expected):
                if name != "operator-a":
                    raise HTTPException(403, "operator_required")
                return AuthenticatedPrincipal(
                    issuer=ISSUER,
                    subject=name,
                    audience=("datacopilot-demo",),
                    authenticated_at=datetime.now(UTC),
                )
        raise HTTPException(401, "invalid_demo_token")

    @router.post("/internal/demo/reports", response_model=ReportView)
    async def report(
        body: ReportRequest,
        current: Annotated[AuthenticatedPrincipal, Depends(principal)],
    ):
        try:
            return await reports.run(body.report_id, current)
        except BusinessAnalyticsError as error:
            raise HTTPException(422, error.code) from None
        except Exception:
            raise HTTPException(503, "report_failed") from None

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "offline", "source": "template"}

    app.include_router(router)
    return app


def create_app():
    path = Path(os.environ["WORKFLOWOPS_DEMO_CONFIG"]).resolve()
    if not path.is_relative_to(ROOT / ".runtime"):
        raise ValueError("demo_config_outside_runtime")
    config = json.loads(path.read_text(encoding="utf-8"))
    if config["owner"] != "workflowops-demo" or config["mode"] != "offline":
        raise ValueError("invalid_demo_config")
    root = Path(config["delivery_root"])
    if not root.resolve().is_relative_to(ROOT / ".runtime"):
        raise ValueError("delivery_root_outside_runtime")
    return demo_app(
        config["datacopilot_tokens"],
        root,
        FileAudit(path.parent / "analytics-audit.jsonl"),
    )
