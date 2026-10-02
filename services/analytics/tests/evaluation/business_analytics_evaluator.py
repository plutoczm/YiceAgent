"""Deterministic D5 golden evaluation over two synthetic Delivery v2 snapshots."""

import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from backend.app.application.business_analytics.errors import BusinessAnalyticsError
from backend.app.application.business_analytics.managed_models import (
    ManagedAnalyticsPolicy,
    ManagedGenericValidation,
    ManagedSQLDraft,
    ManagedTokenUsage,
)
from backend.app.application.business_analytics.managed_text2sql import ManagedText2SQLPipeline
from backend.app.application.business_analytics.models import (
    BusinessAnalyticsRequest,
    BusinessCatalogSnapshot,
)
from backend.app.application.business_analytics.workflow import BusinessAnalyticsWorkflow
from backend.app.application.identity.errors import TenantAccessDeniedError
from backend.app.application.identity.models import AuthenticatedPrincipal
from backend.app.application.text2sql.models import SQLEngine
from backend.app.infrastructure.business_data.contract_catalog import BusinessDataContractCatalog
from tests.api.test_business_analytics_api import close_client, make_client, make_runtime


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = Path(__file__).resolve().parent / "cases/business_analytics_v1.json"
EVALUATION_ISSUER = "https://issuer.test/"
EVALUATION_AUDIENCE = "datacopilot-api"
FULL_DATASET_SCOPE = ("orders_v1", "tickets_v1", "ticket_events_v1", "actions_v1")


class GoldenSQLGenerator:
    def __init__(self, cases: list[dict[str, Any]]) -> None:
        self.sql_by_question = {
            case["question"]: case["sql"]
            for case in cases
            if case.get("layer") == "execution"
        }
        self.sql_by_question.update(
            {
                case["id"]: case["sql"]
                for case in cases
                if case.get("layer") == "sql_scope"
            }
        )

    async def generate(self, request) -> ManagedSQLDraft:
        sql = self.sql_by_question[request.question]
        return ManagedSQLDraft(
            candidate_sql=sql,
            generic_validation=ManagedGenericValidation(is_valid=True),
            token_usage=ManagedTokenUsage(
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            ),
            model_explanation="versioned synthetic SQL candidate",
        )


class DiscardAuditSink:
    def emit(self, event: object) -> None:
        return


class ProxyPatch:
    def __init__(self, stack: ExitStack) -> None:
        self._stack = stack

    def setattr(self, target: object, name: str, value: Any) -> None:
        self._stack.enter_context(patch.object(target, name, value))


def load_cases() -> list[dict[str, Any]]:
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if payload.get("version") != "1.0.0" or payload.get("dataset") != (
        "synthetic_supportops_delivery_v2"
    ):
        raise AssertionError("unsupported evaluation dataset version")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not 30 <= len(cases) <= 50:
        raise AssertionError("golden evaluation must contain 30 to 50 cases")
    ids = [case.get("id") for case in cases]
    if len(ids) != len(set(ids)) or any(not value for value in ids):
        raise AssertionError("evaluation case identifiers must be unique")
    return cases


def run_business_analytics_evaluation() -> dict[str, Any]:
    cases = load_cases()
    generator = GoldenSQLGenerator(cases)
    with tempfile.TemporaryDirectory(prefix="datacopilot-d5-eval-") as directory:
        runtime = make_runtime(
            Path(directory),
            audit_sink=DiscardAuditSink(),
            generator=generator,
        )
        results: dict[str, dict[str, str]] = {}
        _evaluate_routing(cases, results)
        _evaluate_authorization(cases, runtime, results)
        asyncio.run(_evaluate_sql_scope(cases, runtime, generator, results))
        asyncio.run(_evaluate_execution(cases, runtime, results))
        _evaluate_api_auth(cases, Path(directory), results)

    metrics = _metrics(cases, results)
    gate_errors = _gate_errors(metrics)
    return {
        "dataset_version": "1.0.0",
        "total_cases": len(cases),
        "metrics": metrics,
        "gate_passed": not gate_errors,
        "gate_errors": gate_errors,
        "case_results": [
            {"id": case["id"], **results.get(case["id"], {"status": "not_run"})}
            for case in cases
        ],
    }


def _evaluate_routing(
    cases: list[dict[str, Any]],
    results: dict[str, dict[str, str]],
) -> None:
    from backend.app.application.agent.router import IntentRouter

    router = IntentRouter()
    for case in cases:
        if case["layer"] != "routing":
            continue
        actual = router.classify(case["question"]).intent.value
        expected = case["expected_intent"]
        results[case["id"]] = {
            "status": "pass" if actual == expected else "fail",
            "reason": "intent_match" if actual == expected else "intent_mismatch",
        }


def _evaluate_authorization(
    cases: list[dict[str, Any]],
    runtime,
    results: dict[str, dict[str, str]],
) -> None:
    for case in cases:
        if case["layer"] != "authorization":
            continue
        allowed = False
        actual_tenant: str | None = None
        try:
            if "body_extra" in case:
                BusinessAnalyticsRequest.model_validate(
                    {"question": "test authorization"} | case["body_extra"]
                )
            else:
                principal = _principal(case["issuer"], case["subject"])
                grant = runtime.tenant_access_resolver.resolve_access(principal)
                actual_tenant = grant.tenant_id
                allowed = set(case["requested_datasets"]).issubset(
                    set(grant.allowed_datasets)
                )
        except (TenantAccessDeniedError, ValidationError):
            allowed = False
        expected_allow = case["expected"] == "allow"
        passed = allowed is expected_allow
        if expected_allow and actual_tenant != case.get("expected_tenant"):
            passed = False
        results[case["id"]] = {
            "status": "pass" if passed else "fail",
            "reason": "grant_match" if passed else "grant_mismatch",
        }


async def _evaluate_sql_scope(
    cases: list[dict[str, Any]],
    runtime,
    generator: GoldenSQLGenerator,
    results: dict[str, dict[str, str]],
) -> None:
    catalog = BusinessDataContractCatalog(
        contract_path=REPOSITORY_ROOT / "contracts/external/business_data/v1/contract.json",
        acceptance_path=REPOSITORY_ROOT / "contracts/external/business_data/v1/acceptance.json",
    )
    pipeline = ManagedText2SQLPipeline(
        workflow=BusinessAnalyticsWorkflow(catalog_port=catalog),
        generator=generator,
        policy=ManagedAnalyticsPolicy(),
    )
    principal = _principal(EVALUATION_ISSUER, "user-a")
    grant = runtime.tenant_access_resolver.resolve_access(principal)
    for case in cases:
        if case["layer"] != "sql_scope":
            continue
        context = runtime.context_factory.create_context(
            principal=principal,
            grant=grant,
            request_id=str(uuid4()),
            entrypoint="internal",
        )
        accepted = False
        reason = "candidate_rejected"
        try:
            plan = await pipeline.generate(
                request=BusinessAnalyticsRequest(
                    question=case["id"],
                    engine=SQLEngine.HIVE,
                    requested_datasets=tuple(case["allowed_datasets"]),
                ),
                context=context,
            )
            accepted = plan.status.value == "ready"
            reason = "candidate_ready" if accepted else "candidate_not_ready"
        except BusinessAnalyticsError as error:
            reason = error.code
        expected_accept = case["expected"] == "allow"
        passed = accepted is expected_accept
        results[case["id"]] = {
            "status": "pass" if passed else "fail",
            "reason": reason if passed else "sql_scope_outcome_mismatch",
        }


async def _evaluate_execution(
    cases: list[dict[str, Any]],
    runtime,
    results: dict[str, dict[str, str]],
) -> None:
    for case in cases:
        if case["layer"] != "execution":
            continue
        principal = _principal(EVALUATION_ISSUER, case["subject"])
        grant = runtime.tenant_access_resolver.resolve_access(principal)
        context = runtime.context_factory.create_context(
            principal=principal,
            grant=grant,
            request_id=str(uuid4()),
            entrypoint="internal",
        )
        try:
            result = await runtime.workflow.run(
                request=BusinessAnalyticsRequest(
                    question=case["question"],
                    engine=SQLEngine.HIVE,
                    requested_datasets=tuple(case["requested_datasets"]),
                ),
                context=context,
            )
            actual_columns = [column.name for column in result.columns]
            actual_rows = [list(row) for row in result.rows]
            exact = (
                actual_columns == case["expected_columns"]
                and actual_rows == case["expected_rows"]
            )
            isolated = result.snapshot.tenant_id == case["tenant"]
            results[case["id"]] = {
                "status": "pass" if exact and isolated else "fail",
                "reason": (
                    "exact_result_and_tenant"
                    if exact and isolated
                    else "tenant_isolation_violation"
                    if not isolated
                    else "answer_mismatch"
                ),
            }
        except BusinessAnalyticsError as error:
            results[case["id"]] = {
                "status": "fail",
                "reason": error.code,
            }


def _evaluate_api_auth(
    cases: list[dict[str, Any]],
    temporary_directory: Path,
    results: dict[str, dict[str, str]],
) -> None:
    api_cases = [case for case in cases if case["layer"] == "api_auth"]
    if not api_cases:
        return
    from tests.api.test_business_analytics_api import make_client
    from tests.identity_test_support import signed_token

    import urllib.request

    with ExitStack() as stack:
        patcher = ProxyPatch(stack)
        patcher.setattr(urllib.request, "getproxies", lambda: {})
        client, jwks, key, app, _audit, _runtime = make_client(
            temporary_directory,
            patcher,
        )
        try:
            valid = signed_token(key)
            unknown = signed_token(key, subject="no-grant")
            for case in api_cases:
                headers: dict[str, str] = {}
                body: dict[str, Any] = {"question": "Show order status counts"}
                if case["scenario"] == "invalid_bearer":
                    headers["Authorization"] = "Bearer invalid-token"
                elif case["scenario"] == "wrong_scheme":
                    headers["Authorization"] = f"Basic {valid}"
                elif case["scenario"] == "no_tenant_grant":
                    headers["Authorization"] = f"Bearer {unknown}"
                elif case["scenario"] == "tenant_body_override":
                    headers["Authorization"] = f"Bearer {valid}"
                    body["tenant_id"] = "tenant-b"
                elif case["scenario"] == "delivery_path_body_override":
                    headers["Authorization"] = f"Bearer {valid}"
                    body["delivery_path"] = "private/tenant-b"
                response = client.post(
                    "/api/v1/business-analytics/query",
                    headers=headers,
                    json=body,
                )
                passed = response.status_code == case["expected_status"]
                if case["scenario"] in {
                    "tenant_body_override",
                    "delivery_path_body_override",
                }:
                    passed = passed and "tenant-b" not in response.text
                    passed = passed and "private/tenant-b" not in response.text
                results[case["id"]] = {
                    "status": "pass" if passed else "fail",
                    "reason": "expected_http_outcome" if passed else "http_outcome_mismatch",
                }
        finally:
            close_client(client, jwks, app)


def _principal(issuer: str, subject: str) -> AuthenticatedPrincipal:
    from datetime import UTC, datetime

    return AuthenticatedPrincipal(
        issuer=issuer,
        subject=subject,
        audience=(EVALUATION_AUDIENCE,),
        authenticated_at=datetime.now(UTC),
    )


def _metrics(
    cases: list[dict[str, Any]],
    results: dict[str, dict[str, str]],
) -> dict[str, int | float]:
    def subset(layer: str) -> list[dict[str, Any]]:
        return [case for case in cases if case["layer"] == layer]

    def rate(items: list[dict[str, Any]]) -> float:
        return (
            sum(results.get(case["id"], {}).get("status") == "pass" for case in items)
            / len(items)
            if items
            else 1.0
        )

    authorization = subset("authorization")
    allowed = [case for case in authorization if case["expected"] == "allow"]
    denied = [case for case in authorization if case["expected"] == "deny"]
    scope_cases = subset("sql_scope")
    denied_scope = [case for case in scope_cases if case["expected"] == "deny"]
    unsafe_accepted = sum(
        results.get(case["id"], {}).get("reason") == "candidate_ready"
        for case in denied_scope
    )
    execution_cases = subset("execution")
    tenant_violations = sum(
        results.get(case["id"], {}).get("reason") == "tenant_isolation_violation"
        for case in execution_cases
    )
    return {
        "routing_accuracy": rate(subset("routing")),
        "authorization_expected_pass_accuracy": rate(allowed),
        "authorization_expected_deny_accuracy": rate(denied),
        "sql_scope_accuracy": rate(scope_cases),
        "execution_answer_exact_match_rate": rate(execution_cases),
        "tenant_isolation_violations": tenant_violations,
        "unsafe_query_accepted_count": unsafe_accepted,
        "api_auth_negative_pass_rate": rate(subset("api_auth")),
    }


def _gate_errors(metrics: dict[str, int | float]) -> list[str]:
    errors: list[str] = []
    if metrics["routing_accuracy"] < 0.9:
        errors.append("routing_accuracy_below_90_percent")
    if metrics["authorization_expected_pass_accuracy"] != 1.0:
        errors.append("authorization_allow_accuracy_below_100_percent")
    if metrics["authorization_expected_deny_accuracy"] != 1.0:
        errors.append("authorization_deny_accuracy_below_100_percent")
    if metrics["sql_scope_accuracy"] != 1.0:
        errors.append("sql_scope_accuracy_below_100_percent")
    if metrics["execution_answer_exact_match_rate"] != 1.0:
        errors.append("execution_exact_match_below_100_percent")
    if metrics["tenant_isolation_violations"] != 0:
        errors.append("tenant_isolation_violation_detected")
    if metrics["unsafe_query_accepted_count"] != 0:
        errors.append("unsafe_query_accepted")
    if metrics["api_auth_negative_pass_rate"] != 1.0:
        errors.append("api_auth_negative_pass_rate_below_100_percent")
    return errors


def write_json_report(report: dict[str, Any], path: Path) -> None:
    """Write the safe summary; case SQL, prompts, credentials, and data rows are omitted."""
    safe_report = {
        key: value for key, value in report.items() if key != "case_results"
    }
    safe_report["case_results"] = [
        {"id": item["id"], "status": item["status"], "reason": item["reason"]}
        for item in report["case_results"]
    ]
    path.write_text(json.dumps(safe_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
