"""Optional live Text2SQL quality probe; never runs unless explicitly enabled."""

import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from uuid import uuid4

from backend.app.application.business_analytics.models import BusinessAnalyticsRequest
from backend.app.application.identity.models import AuthenticatedPrincipal
from backend.app.composition.business_analytics import build_trusted_business_analytics_runtime
from backend.app.core.config import get_settings
from backend.app.core.settings import (
    BusinessAnalyticsRuntimeSettings,
    TrustedIdentitySettings,
)
from backend.app.presentation.api.dependencies.providers import get_text2sql_service
from tests.evaluation.business_analytics_evaluator import load_cases


def main() -> int:
    if os.environ.get("DATACOPILOT_EVAL_LIVE_LLM", "").strip().lower() != "true":
        print("Live LLM evaluation is disabled; set DATACOPILOT_EVAL_LIVE_LLM=true to opt in.")
        return 0
    settings = get_settings().model_copy(deep=True)
    settings.identity = TrustedIdentitySettings(
        enabled=True,
        issuer="https://live-evaluation.invalid/",
        audience="datacopilot-live-eval",
        jwks_url="https://live-evaluation.invalid/.well-known/jwks.json",
    )
    fixture = Path(__file__).resolve().parents[1] / (
        "tests/fixtures/business_data/evaluation_v1/tenant-a"
    )
    settings.business_analytics = BusinessAnalyticsRuntimeSettings(
        enabled=True,
        tenant_grants_json=json.dumps(
            [
                {
                    "issuer": "https://live-evaluation.invalid/",
                    "subject": "live-evaluation-subject",
                    "tenant_id": "tenant-a",
                    "allowed_datasets": [
                        "orders_v1",
                        "tickets_v1",
                        "ticket_events_v1",
                        "actions_v1",
                    ],
                    "contract_name": "supportops_business_data",
                    "contract_version": "v1",
                }
            ]
        ),
        tenant_delivery_directories_json=json.dumps({"tenant-a": str(fixture)}),
    )
    try:
        runtime = build_trusted_business_analytics_runtime(
            settings,
            text2sql_service=get_text2sql_service(),
        )
    except Exception:
        print("Live evaluation setup failed; no credentials or exception details were emitted.")
        return 1

    principal = AuthenticatedPrincipal(
        issuer="https://live-evaluation.invalid/",
        subject="live-evaluation-subject",
        audience=("datacopilot-live-eval",),
        authenticated_at=datetime.now(UTC),
    )
    grant = runtime.tenant_access_resolver.resolve_access(principal)
    cases = [
        case
        for case in load_cases()
        if case["layer"] == "execution" and case["tenant"] == "tenant-a"
    ][:5]
    summary: list[dict[str, str]] = []
    for case in cases:
        context = runtime.context_factory.create_context(
            principal=principal,
            grant=grant,
            request_id=str(uuid4()),
            entrypoint="internal",
        )
        try:
            result = asyncio.run(
                runtime.workflow.run(
                    request=BusinessAnalyticsRequest(
                        question=case["question"],
                        requested_datasets=tuple(case["requested_datasets"]),
                    ),
                    context=context,
                )
            )
            matches = [column.name for column in result.columns] == case["expected_columns"]
            matches = matches and [list(row) for row in result.rows] == case["expected_rows"]
            summary.append(
                {"id": case["id"], "status": "exact_match" if matches else "mismatch"}
            )
        except Exception:
            summary.append({"id": case["id"], "status": "generation_or_execution_failed"})
    exact = sum(case["status"] == "exact_match" for case in summary)
    print(f"live_generation_cases: {len(summary)}")
    print(f"execution_exact_matches: {exact}/{len(summary)}")
    print("case_results: " + json.dumps(summary, sort_keys=True))
    print("This live probe does not evaluate explanation faithfulness and is not a CI gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
