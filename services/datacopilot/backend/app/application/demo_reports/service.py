"""Closed report IDs become SQL candidates and pass the existing D1-D4 pipeline."""

from datetime import UTC, datetime
from typing import Literal

from backend.app.application.business_analytics.governed_models import (
    GovernedBusinessAnalyticsResult,
)
from backend.app.application.business_analytics.managed_models import (
    ManagedGenericValidation,
    ManagedSQLDraft,
)
from backend.app.application.business_analytics.models import BusinessAnalyticsRequest
from backend.app.application.text2sql.models import SQLEngine
from pydantic import BaseModel, ConfigDict

ReportId = Literal[
    "orders_total",
    "refund_requests_by_status",
    "pending_approval_count",
    "tickets_by_status",
    "overdue_open_tickets",
]
TEMPLATES = {
    "orders_total": "SELECT COUNT(*) AS orders_total FROM orders_v1",
    "refund_requests_by_status": (
        "SELECT action_status, COUNT(*) AS request_count FROM actions_v1 "
        "WHERE action_kind = 'refund' GROUP BY action_status ORDER BY action_status"
    ),
    "pending_approval_count": (
        "SELECT COUNT(*) AS pending_approval_count FROM actions_v1 "
        "WHERE action_status = 'pending_approval'"
    ),
    "tickets_by_status": (
        "SELECT status, COUNT(*) AS ticket_count FROM tickets_v1 GROUP BY status ORDER BY status"
    ),
    "overdue_open_tickets": (
        "SELECT COUNT(*) AS overdue_open_tickets FROM tickets_v1 "
        "WHERE status NOT IN ('resolved', 'closed') AND sla_due_at < '{as_of}'"
    ),
}


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: ReportId


class ReportView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: ReportId
    template_version: Literal["v1"] = "v1"
    source: Literal["template"] = "template"
    as_of: str
    data: GovernedBusinessAnalyticsResult


class TemplateGenerator:
    def __init__(self, report_id: ReportId, as_of: datetime):
        if report_id not in TEMPLATES or as_of.tzinfo is None:
            raise ValueError("invalid_template_context")
        self.sql = TEMPLATES[report_id].format(
            as_of=as_of.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        )
        self.report_id = report_id

    async def generate(self, request):
        if request.question != self.report_id or request.engine != SQLEngine.HIVE:
            raise ValueError("template_intent_mismatch")
        return ManagedSQLDraft(
            candidate_sql=self.sql,
            generic_validation=ManagedGenericValidation(is_valid=True),
        )


async def execute_report(
    workflow, report_id: ReportId, context, as_of: datetime
) -> ReportView:
    result = await workflow.run(
        request=BusinessAnalyticsRequest(question=report_id, engine=SQLEngine.HIVE),
        context=context,
    )
    return ReportView(
        report_id=report_id, as_of=as_of.isoformat().replace("+00:00", "Z"), data=result
    )
