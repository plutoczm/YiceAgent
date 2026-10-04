from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build_container
from app.main import create_app

CUSTOMER_HEADERS = {
    "X-Principal-Id": "slo-user",
    "X-Customer-Id": "CUST-001",
    "X-Roles": "customer",
}
REQUESTS_PER_SCENARIO = 30
LOCAL_HTTP_P95_BUDGET_MS = 750.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _request(
    client: TestClient,
    *,
    conversation_id: str,
    message: str,
) -> tuple[int, float, bool]:
    started = perf_counter()
    response = client.post(
        "/v1/support/messages",
        headers=CUSTOMER_HEADERS,
        json={"conversation_id": conversation_id, "message": message},
    )
    duration_ms = (perf_counter() - started) * 1000.0
    payload = response.json()
    degraded = bool(payload.get("retrieval_degraded", False))
    return response.status_code, duration_ms, degraded


def main() -> None:
    durations: list[float] = []
    scenario_durations: dict[str, list[float]] = {"knowledge": [], "order": []}
    errors = 0
    degraded_responses = 0

    with (
        TemporaryDirectory(prefix="supportops-slo-") as tmp,
        ExitStack() as resources,
    ):
        database = Path(tmp) / "slo.db"
        settings = Settings(
            database_url=f"sqlite:///{database}",
            support_message_rate_limit=0,
            action_confirmation_rate_limit=0,
            metrics_enabled=True,
            tracing_enabled=True,
            app_env="development",
            auth_mode="dev",
            mcp_auth_mode="disabled",
            seed_demo_data=True,
        )
        container = build_container(settings)
        # Release pooled SQLite handles before temporary-directory cleanup on Windows.
        resources.callback(container.store.engine.dispose)
        with TestClient(create_app(container)) as client:
            for index in range(REQUESTS_PER_SCENARIO):
                status, duration, degraded = _request(
                    client,
                    conversation_id=f"SLO-K-{index}",
                    message="退款政策是什么？",
                )
                durations.append(duration)
                scenario_durations["knowledge"].append(duration)
                errors += int(status != 200)
                degraded_responses += int(degraded)

                status, duration, degraded = _request(
                    client,
                    conversation_id=f"SLO-O-{index}",
                    message="订单状态 ORD-1001",
                )
                durations.append(duration)
                scenario_durations["order"].append(duration)
                errors += int(status != 200)
                degraded_responses += int(degraded)

            metrics = client.get("/metrics")
            metrics_body = metrics.text

    request_count = len(durations)
    success_rate = (request_count - errors) / max(request_count, 1)
    report = {
        "benchmark_version": "supportops-local-observability-slo-v1",
        "scope": "deterministic local CI workload; not a production latency claim",
        "requests": request_count,
        "success_rate": round(success_rate, 6),
        "error_count": errors,
        "degraded_responses": degraded_responses,
        "http_p50_ms": round(_percentile(durations, 0.50), 3),
        "http_p95_ms": round(_percentile(durations, 0.95), 3),
        "knowledge_p95_ms": round(_percentile(scenario_durations["knowledge"], 0.95), 3),
        "order_p95_ms": round(_percentile(scenario_durations["order"], 0.95), 3),
        "local_http_p95_budget_ms": LOCAL_HTTP_P95_BUDGET_MS,
        "metrics_contract": {
            "http_histogram": "supportops_http_request_duration_seconds" in metrics_body,
            "operation_histogram": "supportops_operation_duration_seconds" in metrics_body,
            "degradation_counter": "supportops_degradation_total" in metrics_body,
            "router_operation": 'operation="agent.route"' in metrics_body,
            "retrieval_operation": 'operation="retrieval.hybrid.search"' in metrics_body,
            "tool_operation": 'operation="tool.order.get"' in metrics_body,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if request_count < REQUESTS_PER_SCENARIO * 2:
        raise SystemExit("SLO workload did not execute the expected request count")
    if success_rate != 1.0 or errors:
        raise SystemExit("SLO workload had HTTP failures")
    if degraded_responses:
        raise SystemExit("deterministic local SLO workload unexpectedly degraded")
    if report["http_p95_ms"] > LOCAL_HTTP_P95_BUDGET_MS:
        raise SystemExit("local deterministic HTTP p95 exceeded the CI regression budget")
    if not all(report["metrics_contract"].values()):
        raise SystemExit("observability metrics contract is incomplete")


if __name__ == "__main__":
    main()
