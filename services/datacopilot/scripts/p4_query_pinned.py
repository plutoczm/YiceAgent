"""Acceptance-only D1-D4 query of a previous immutable test delivery."""

import asyncio
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.application.business_analytics.governed_workflow import (
    GovernedBusinessAnalyticsWorkflow,
)
from backend.app.application.business_analytics.managed_models import (
    ManagedAnalyticsPolicy,
)
from backend.app.application.business_analytics.managed_text2sql import (
    ManagedText2SQLPipeline,
)
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsContext,
    BusinessAnalyticsRequest,
)
from backend.app.application.business_analytics.workflow import (
    BusinessAnalyticsWorkflow,
)
from backend.app.application.demo_reports.service import TemplateGenerator
from backend.app.infrastructure.business_analytics.delivery_v2 import (
    BusinessDataDeliveryV2Consumer,
    FileBusinessDataDeliveryResolver,
)
from backend.app.infrastructure.business_analytics.duckdb_executor import (
    DuckDBBusinessAnalyticsExecutor,
)
from backend.app.infrastructure.business_analytics.logging_audit import (
    LoggingBusinessAnalyticsAuditSink,
)
from backend.app.infrastructure.business_data.contract_catalog import (
    BusinessDataContractCatalog,
)


async def main():
    config = json.loads(os.environ["WORKFLOWOPS_P4_PINNED"])
    project = Path(__file__).resolve().parents[3]
    root = Path(config["root"])
    if (
        not root.resolve().is_relative_to(project / ".runtime")
        or re.fullmatch(r"[a-f0-9]{32}", config["export_id"]) is None
    ):
        raise ValueError("invalid_pinned_test_directory")
    catalog = BusinessDataContractCatalog.from_repository_root(
        Path(__file__).resolve().parents[1]
    )
    contract = catalog.load_accepted_contract().contract
    context = BusinessAnalyticsContext(
        request_id=str(uuid4()),
        tenant_id="tenant-a",
        contract_name=contract.contract_name,
        contract_version=contract.version,
        allowed_datasets=tuple(d.name for d in contract.datasets),
    )
    consumer = BusinessDataDeliveryV2Consumer(
        delivery_resolver=FileBusinessDataDeliveryResolver(
            tenant_delivery_directories={
                "tenant-a": root / "tenant-a" / config["export_id"]
            }
        ),
        contract_catalog=catalog,
    )
    policy = ManagedAnalyticsPolicy()
    workflow = GovernedBusinessAnalyticsWorkflow(
        text2sql_pipeline=ManagedText2SQLPipeline(
            workflow=BusinessAnalyticsWorkflow(catalog_port=catalog),
            generator=TemplateGenerator("refund_requests_by_status", datetime.now(UTC)),
            policy=policy,
        ),
        execution_port=DuckDBBusinessAnalyticsExecutor(
            delivery_consumer=consumer, catalog_port=catalog, managed_policy=policy
        ),
        audit_sink=LoggingBusinessAnalyticsAuditSink(),
    )
    result = await workflow.run(
        request=BusinessAnalyticsRequest(
            question="refund_requests_by_status", engine="hive"
        ),
        context=context,
    )
    print(result.model_dump_json())


if __name__ == "__main__":
    asyncio.run(main())
