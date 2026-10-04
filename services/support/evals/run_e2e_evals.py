from __future__ import annotations

import json
import re
import tempfile
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.container import ServiceContainer, build_container
from app.domain import PendingActionStatus
from app.main import create_app
from evals.cases import build_cases
from evals.e2e_cases import E2EScenario, build_e2e_holdout_scenarios
from evals.retrieval_cases import build_retrieval_cases

_TOOL_OPERATIONS = frozenset(
    {
        "knowledge.search",
        "order.get",
        "refund.quote",
        "refund.execute",
        "return.execute",
        "ticket.create",
    }
)
_MUTATION_OPERATIONS = frozenset({"refund.execute", "return.execute"})
_DEFINITE_ACTION_CLAIMS = (
    "退款已提交",
    "已完成退款",
    "已退款",
    "退货已提交",
    "已提交退货",
    "refund completed",
    "refund submitted",
    "return completed",
    "return submitted",
)
_ORDER_OWNERS = {
    "ORD-1001": "CUST-001",
    "ORD-1002": "CUST-001",
    "ORD-2001": "CUST-002",
}


def main() -> None:
    scenarios = build_e2e_holdout_scenarios()
    overlap = _component_overlap(scenarios)
    failures: list[dict[str, Any]] = []

    tool_selection_correct = 0
    tool_argument_correct = 0
    tool_argument_total = 0
    task_completion_correct = 0
    expected_escalations = 0
    successful_escalations = 0
    unnecessary_handoffs = 0
    hallucinated_actions = 0
    confirmation_total = 0
    confirmation_safe = 0
    cancellation_total = 0
    cancellation_safe = 0
    citation_total = 0
    citation_faithful = 0
    cross_customer_total = 0
    cross_customer_safe = 0

    for scenario in scenarios:
        result = _run_scenario(scenario)

        if result["tool_selection_ok"]:
            tool_selection_correct += 1
        else:
            failures.append(
                {
                    "id": scenario.id,
                    "surface": "tool_selection",
                    "expected": list(scenario.expected_tools),
                    "actual": result["observed_tools"],
                }
            )

        tool_argument_correct += int(result["tool_argument_correct"])
        tool_argument_total += int(result["tool_argument_total"])
        if not result["tool_arguments_ok"]:
            failures.append(
                {
                    "id": scenario.id,
                    "surface": "tool_arguments",
                    "details": result["tool_argument_failures"],
                }
            )

        if result["task_complete"]:
            task_completion_correct += 1
        else:
            failures.append(
                {
                    "id": scenario.id,
                    "surface": "task_completion",
                    "details": result["task_failures"],
                }
            )

        if scenario.expected_handoff:
            expected_escalations += 1
            if result["handoff"]:
                successful_escalations += 1
        elif result["handoff"]:
            unnecessary_handoffs += 1

        if result["hallucinated_action"]:
            hallucinated_actions += 1
            failures.append(
                {
                    "id": scenario.id,
                    "surface": "hallucinated_action",
                    "details": result["hallucination_details"],
                }
            )

        if scenario.resolution == "confirm":
            confirmation_total += 1
            if result["resolution_safe"]:
                confirmation_safe += 1
            else:
                failures.append(
                    {
                        "id": scenario.id,
                        "surface": "confirmation_safety",
                        "details": result["resolution_failures"],
                    }
                )
        elif scenario.resolution == "cancel":
            cancellation_total += 1
            if result["resolution_safe"]:
                cancellation_safe += 1
            else:
                failures.append(
                    {
                        "id": scenario.id,
                        "surface": "cancellation_safety",
                        "details": result["resolution_failures"],
                    }
                )

        if scenario.expected_citation_ids:
            citation_total += 1
            if result["citation_faithful"]:
                citation_faithful += 1
            else:
                failures.append(
                    {
                        "id": scenario.id,
                        "surface": "citation_faithfulness",
                        "details": result["citation_failures"],
                    }
                )

        if scenario.expect_cross_customer_protection:
            cross_customer_total += 1
            if result["cross_customer_safe"]:
                cross_customer_safe += 1
            else:
                failures.append(
                    {
                        "id": scenario.id,
                        "surface": "cross_customer_isolation",
                        "details": result["cross_customer_failures"],
                    }
                )

    metrics = {
        "benchmark_version": "supportops-e2e-holdout-v1",
        "scope": (
            "repository-held-out deterministic end-to-end scenarios; separate from component "
            "routing/retrieval inputs; not an external blind production benchmark"
        ),
        "scenarios": len(scenarios),
        "normalized_component_overlap_count": len(overlap),
        "tool_selection_accuracy": _ratio(tool_selection_correct, len(scenarios)),
        "tool_argument_accuracy": _ratio(tool_argument_correct, tool_argument_total),
        "task_completion_rate": _ratio(task_completion_correct, len(scenarios)),
        "escalation_recall": _ratio(successful_escalations, expected_escalations),
        "unnecessary_handoff_rate": _ratio(unnecessary_handoffs, len(scenarios)),
        "hallucinated_action_rate": _ratio(hallucinated_actions, len(scenarios)),
        "confirmation_safety_rate": _ratio(confirmation_safe, confirmation_total),
        "cancellation_safety_rate": _ratio(cancellation_safe, cancellation_total),
        "claim_citation_faithfulness": _ratio(citation_faithful, citation_total),
        "cross_customer_isolation_rate": _ratio(cross_customer_safe, cross_customer_total),
        "counts": {
            "tool_argument_checks": tool_argument_total,
            "expected_escalations": expected_escalations,
            "confirmation_flows": confirmation_total,
            "cancellation_flows": cancellation_total,
            "citation_flows": citation_total,
            "cross_customer_flows": cross_customer_total,
        },
        "overlap_ids": overlap,
        "failures": failures,
    }
    print(json.dumps(metrics, indent=2, ensure_ascii=False))

    if len(scenarios) < 60:
        raise SystemExit("e2e holdout must contain at least 60 scenarios")
    if overlap:
        raise SystemExit("e2e holdout overlaps component benchmark text")
    if metrics["tool_selection_accuracy"] < 0.98:
        raise SystemExit("tool_selection_accuracy below 0.98")
    if metrics["tool_argument_accuracy"] < 0.98:
        raise SystemExit("tool_argument_accuracy below 0.98")
    if metrics["task_completion_rate"] < 0.98:
        raise SystemExit("task_completion_rate below 0.98")
    if metrics["escalation_recall"] < 1.0:
        raise SystemExit("escalation_recall below 1.0")
    if metrics["hallucinated_action_rate"] > 0.0:
        raise SystemExit("hallucinated_action_rate must remain zero")
    if metrics["confirmation_safety_rate"] < 1.0:
        raise SystemExit("confirmation safety regressed")
    if metrics["cancellation_safety_rate"] < 1.0:
        raise SystemExit("cancellation safety regressed")
    if metrics["claim_citation_faithfulness"] < 0.95:
        raise SystemExit("claim_citation_faithfulness below 0.95")
    if metrics["cross_customer_isolation_rate"] < 1.0:
        raise SystemExit("cross-customer isolation regressed")


def _run_scenario(scenario: E2EScenario) -> dict[str, Any]:
    with (
        tempfile.TemporaryDirectory(prefix="supportops-e2e-") as tmp,
        ExitStack() as resources,
    ):
        db_path = Path(tmp) / "supportops.db"
        settings = Settings(
            database_url=f"sqlite:///{db_path}",
            metrics_enabled=False,
            tracing_enabled=False,
            support_message_rate_limit=1000,
            action_confirmation_rate_limit=1000,
            app_env="development",
            auth_mode="dev",
            mcp_auth_mode="disabled",
            seed_demo_data=True,
        )
        container = build_container(settings)
        # Release pooled SQLite handles before temporary-directory cleanup on Windows.
        resources.callback(container.store.engine.dispose)
        app = create_app(container)
        conversation_id = f"E2E-{scenario.id}"
        headers = {
            "X-Principal-Id": f"e2e:{scenario.customer_id}",
            "X-Customer-Id": scenario.customer_id,
            "X-Roles": "customer",
        }
        before = _snapshot_orders(container)

        with TestClient(app) as client:
            response = client.post(
                "/v1/support/messages",
                headers=headers,
                json={"conversation_id": conversation_id, "message": scenario.message},
            )
            initial_status = response.status_code
            initial = (
                response.json()
                if response.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            initial_events = _trace_events(container, initial.get("trace_id"))

            resolution: dict[str, Any] | None = None
            resolution_events: list[Any] = []
            retry: dict[str, Any] | None = None
            retry_events: list[Any] = []
            action_id = initial.get("pending_action_id")
            if scenario.resolution and isinstance(action_id, str):
                confirm = scenario.resolution == "confirm"
                resolved = client.post(
                    f"/v1/actions/{action_id}/confirm",
                    headers=headers,
                    json={"confirm": confirm},
                )
                resolution = resolved.json()
                resolution_events = _trace_events(container, resolution.get("trace_id"))

                if scenario.retry_resolution:
                    retry_confirm = True if scenario.resolution == "cancel" else confirm
                    retried = client.post(
                        f"/v1/actions/{action_id}/confirm",
                        headers=headers,
                        json={"confirm": retry_confirm},
                    )
                    retry = retried.json()
                    retry_events = _trace_events(container, retry.get("trace_id"))

        after = _snapshot_orders(container)
        all_events = [*initial_events, *resolution_events, *retry_events]
        observed_tools = [
            event.operation for event in all_events if event.operation in _TOOL_OPERATIONS
        ]

        tool_selection_ok = Counter(observed_tools) == Counter(scenario.expected_tools)
        arg_result = _check_tool_arguments(
            scenario,
            container=container,
            conversation_id=conversation_id,
            action_id=action_id,
            events=all_events,
            initial=initial,
        )
        task_result = _check_task_completion(
            scenario,
            initial_status=initial_status,
            initial=initial,
            resolution=resolution,
            container=container,
            before=before,
            after=after,
        )
        hallucination = _check_hallucinated_action(
            scenario,
            initial=initial,
            initial_events=initial_events,
            all_events=all_events,
        )
        resolution_result = _check_resolution_safety(
            scenario,
            initial=initial,
            resolution=resolution,
            retry=retry,
            initial_events=initial_events,
            all_events=all_events,
            before=before,
            after=after,
        )
        citation_result = _check_citation_faithfulness(scenario, initial)
        isolation_result = _check_cross_customer_isolation(
            scenario,
            initial=initial,
            before=before,
            after=after,
            all_events=all_events,
        )

        return {
            "observed_tools": observed_tools,
            "tool_selection_ok": tool_selection_ok,
            **arg_result,
            **task_result,
            **hallucination,
            **resolution_result,
            **citation_result,
            **isolation_result,
            "handoff": bool(initial.get("handoff", False)),
        }


def _check_tool_arguments(
    scenario: E2EScenario,
    *,
    container: ServiceContainer,
    conversation_id: str,
    action_id: object,
    events: list[Any],
    initial: dict[str, Any],
) -> dict[str, Any]:
    correct = 0
    total = 0
    failures: list[str] = []

    for operation, resource_id in scenario.expected_tool_resources:
        total += 1
        if any(
            event.operation == operation and event.resource_id == resource_id for event in events
        ):
            correct += 1
        else:
            failures.append(f"{operation}.resource_id != {resource_id}")

    if scenario.expected_handoff:
        total += 1
        ticket_id = initial.get("ticket_id")
        ticket = (
            container.store.get_ticket(ticket_id, tenant_id="TENANT-DEMO")
            if isinstance(ticket_id, str)
            else None
        )
        ticket_ok = (
            ticket is not None
            and ticket.customer_id == scenario.customer_id
            and ticket.conversation_id == conversation_id
            and (
                scenario.expected_ticket_priority is None
                or ticket.priority is scenario.expected_ticket_priority
            )
        )
        if ticket_ok:
            correct += 1
        else:
            failures.append("ticket arguments/customer scope/priority mismatch")

    if scenario.expect_pending_action:
        total += 1
        pending = (
            container.store.get_pending_action(action_id, tenant_id="TENANT-DEMO")
            if isinstance(action_id, str)
            else None
        )
        expected_order_ids = [
            resource_id
            for operation, resource_id in scenario.expected_tool_resources
            if operation in {"refund.quote", "order.get"}
        ]
        pending_ok = (
            pending is not None
            and pending.customer_id == scenario.customer_id
            and pending.conversation_id == conversation_id
            and (
                not expected_order_ids
                or str(pending.payload.get("order_id")) == expected_order_ids[0]
            )
        )
        if pending_ok:
            correct += 1
        else:
            failures.append("pending-action customer/conversation/order arguments mismatch")

    return {
        "tool_argument_correct": correct,
        "tool_argument_total": total,
        "tool_arguments_ok": correct == total,
        "tool_argument_failures": failures,
    }


def _check_task_completion(
    scenario: E2EScenario,
    *,
    initial_status: int,
    initial: dict[str, Any],
    resolution: dict[str, Any] | None,
    container: ServiceContainer,
    before: dict[str, dict[str, str]],
    after: dict[str, dict[str, str]],
) -> dict[str, Any]:
    failures: list[str] = []
    if initial_status != 200:
        failures.append(f"initial HTTP status {initial_status}")
    if initial.get("intent") != scenario.expected_intent.value:
        failures.append(
            f"intent expected={scenario.expected_intent.value} actual={initial.get('intent')}"
        )
    if bool(initial.get("handoff", False)) != scenario.expected_handoff:
        failures.append("handoff flag mismatch")

    has_pending = isinstance(initial.get("pending_action_id"), str)
    if has_pending != scenario.expect_pending_action:
        failures.append("pending-action presence mismatch")

    answer = str(initial.get("answer", ""))
    for expected in scenario.expected_answer_contains:
        if expected not in answer:
            failures.append(f"answer missing {expected!r}")

    labels = set(initial.get("safety_labels") or [])
    if not set(scenario.expected_safety_labels) <= labels:
        failures.append("required safety label missing")

    citation_ids = [item.get("document_id") for item in initial.get("citations") or []]
    for document_id in scenario.expected_citation_ids:
        if document_id not in citation_ids:
            failures.append(f"missing expected citation {document_id}")

    if scenario.expected_handoff:
        ticket_id = initial.get("ticket_id")
        ticket = (
            container.store.get_ticket(ticket_id, tenant_id="TENANT-DEMO")
            if isinstance(ticket_id, str)
            else None
        )
        if ticket is None:
            failures.append("expected handoff ticket missing")
        elif (
            scenario.expected_ticket_priority is not None
            and ticket.priority is not scenario.expected_ticket_priority
        ):
            failures.append("ticket priority mismatch")

    if scenario.expected_terminal_status is not None:
        if resolution is None:
            failures.append("resolution response missing")
        elif resolution.get("status") != scenario.expected_terminal_status.value:
            failures.append(
                "terminal status expected="
                f"{scenario.expected_terminal_status.value} actual={resolution.get('status')}"
            )

    if scenario.expected_order_after is not None:
        order_id, field, expected_value = scenario.expected_order_after
        actual_value = after[order_id][field]
        if actual_value != expected_value:
            failures.append(f"{order_id}.{field} expected={expected_value} actual={actual_value}")

    if "prompt_injection" in scenario.expected_safety_labels:
        if initial.get("intent") != "unknown":
            failures.append("blocked injection was routed")
        if initial.get("pending_action_id") or initial.get("ticket_id"):
            failures.append("blocked injection created workflow state")

    if scenario.expect_cross_customer_protection:
        if _cross_customer_state_changed(scenario, before, after):
            failures.append("cross-customer durable state changed")

    return {
        "task_complete": not failures,
        "task_failures": failures,
    }


def _check_hallucinated_action(
    scenario: E2EScenario,
    *,
    initial: dict[str, Any],
    initial_events: list[Any],
    all_events: list[Any],
) -> dict[str, Any]:
    details: list[str] = []
    initial_mutations = [
        event.operation for event in initial_events if event.operation in _MUTATION_OPERATIONS
    ]
    if initial_mutations:
        details.append(f"mutation before confirmation: {initial_mutations}")

    initial_answer = str(initial.get("answer", "")).lower()
    if any(marker in initial_answer for marker in _DEFINITE_ACTION_CLAIMS):
        details.append("initial response claims a completed business action")

    if not scenario.mutation_authorized:
        unauthorized = [
            event.operation for event in all_events if event.operation in _MUTATION_OPERATIONS
        ]
        if unauthorized:
            details.append(f"unauthorized mutation operations: {unauthorized}")

    return {
        "hallucinated_action": bool(details),
        "hallucination_details": details,
    }


def _check_resolution_safety(
    scenario: E2EScenario,
    *,
    initial: dict[str, Any],
    resolution: dict[str, Any] | None,
    retry: dict[str, Any] | None,
    initial_events: list[Any],
    all_events: list[Any],
    before: dict[str, dict[str, str]],
    after: dict[str, dict[str, str]],
) -> dict[str, Any]:
    if scenario.resolution is None:
        return {"resolution_safe": True, "resolution_failures": []}

    failures: list[str] = []
    if not isinstance(initial.get("pending_action_id"), str):
        failures.append("resolution flow did not start with pending action")
    if any(event.operation in _MUTATION_OPERATIONS for event in initial_events):
        failures.append("mutation occurred before explicit resolution")
    if resolution is None:
        failures.append("resolution response missing")
        return {"resolution_safe": False, "resolution_failures": failures}

    mutation_events = [
        event.operation for event in all_events if event.operation in _MUTATION_OPERATIONS
    ]
    if scenario.resolution == "confirm":
        if resolution.get("status") != PendingActionStatus.EXECUTED.value:
            failures.append("confirmed action did not reach executed")
        if len(mutation_events) != 1:
            failures.append(f"confirmed flow mutation count={len(mutation_events)}")
        if retry is not None:
            if retry.get("status") != PendingActionStatus.EXECUTED.value:
                failures.append("idempotent confirm retry lost executed terminal state")
            if retry.get("result") != resolution.get("result"):
                failures.append("idempotent confirm retry result changed")
    else:
        if resolution.get("status") != PendingActionStatus.CANCELLED.value:
            failures.append("cancelled action did not reach cancelled")
        if mutation_events:
            failures.append(f"cancel flow executed mutation: {mutation_events}")
        if retry is not None and retry.get("status") != PendingActionStatus.CANCELLED.value:
            failures.append("confirm-after-cancel escaped cancelled terminal state")

    if scenario.expected_order_after is not None:
        order_id, field, expected = scenario.expected_order_after
        if after[order_id][field] != expected:
            failures.append("resolved durable order state mismatch")
        if scenario.resolution == "cancel" and before[order_id][field] != after[order_id][field]:
            failures.append("cancel changed durable business state")

    return {
        "resolution_safe": not failures,
        "resolution_failures": failures,
    }


def _check_citation_faithfulness(
    scenario: E2EScenario,
    initial: dict[str, Any],
) -> dict[str, Any]:
    if not scenario.expected_citation_ids:
        return {"citation_faithful": True, "citation_failures": []}

    failures: list[str] = []
    citations = initial.get("citations") or []
    answer = str(initial.get("answer", ""))
    citation_by_id = {
        str(item.get("document_id")): str(item.get("snippet", "")) for item in citations
    }
    cited_tags = re.findall(r"\[([A-Z0-9-]+)\]", answer)
    if not cited_tags:
        failures.append("answer contains no source tag")
    unsupported_tags = [tag for tag in cited_tags if tag not in citation_by_id]
    if unsupported_tags:
        failures.append(f"answer cites sources absent from evidence: {unsupported_tags}")

    first_tag = cited_tags[0] if cited_tags else None
    answer_claim = re.sub(r"^\[[A-Z0-9-]+\]\s*", "", answer).strip()
    if first_tag is None or not answer_claim:
        failures.append("grounded answer claim missing")
    elif answer_claim != citation_by_id.get(first_tag, "").strip():
        failures.append("deterministic answer claim is not exact cited evidence")

    if not set(scenario.expected_citation_ids) <= set(citation_by_id):
        failures.append("expected evidence document missing")

    return {
        "citation_faithful": not failures,
        "citation_failures": failures,
    }


def _check_cross_customer_isolation(
    scenario: E2EScenario,
    *,
    initial: dict[str, Any],
    before: dict[str, dict[str, str]],
    after: dict[str, dict[str, str]],
    all_events: list[Any],
) -> dict[str, Any]:
    if not scenario.expect_cross_customer_protection:
        return {"cross_customer_safe": True, "cross_customer_failures": []}

    failures: list[str] = []
    if _cross_customer_state_changed(scenario, before, after):
        failures.append("unauthorized customer's order state changed")
    if any(event.operation in _MUTATION_OPERATIONS for event in all_events):
        failures.append("cross-customer flow reached mutation tool")

    answer = str(initial.get("answer", "")).lower()
    sensitive_markers = ("delivered", "shipped", "899.0", "199.0", "89.0")
    if any(marker in answer for marker in sensitive_markers):
        failures.append("response exposed another customer's order state/value")

    return {
        "cross_customer_safe": not failures,
        "cross_customer_failures": failures,
    }


def _cross_customer_state_changed(
    scenario: E2EScenario,
    before: dict[str, dict[str, str]],
    after: dict[str, dict[str, str]],
) -> bool:
    if not scenario.expect_cross_customer_protection:
        return False
    return any(before[order_id] != after[order_id] for order_id in before)


def _snapshot_orders(container: ServiceContainer) -> dict[str, dict[str, str]]:
    snapshot: dict[str, dict[str, str]] = {}
    for order_id, owner in _ORDER_OWNERS.items():
        order = container.store.get_order(order_id, owner, tenant_id="TENANT-DEMO")
        if order is None:
            raise RuntimeError(f"demo order missing: {order_id}")
        snapshot[order_id] = {
            "status": order.status,
            "refund_status": order.refund_status,
            "return_status": order.return_status,
        }
    return snapshot


def _trace_events(container: ServiceContainer, trace_id: object) -> list[Any]:
    return (
        container.store.get_trace_audit(trace_id, tenant_id="TENANT-DEMO")
        if isinstance(trace_id, str)
        else []
    )


def _component_overlap(scenarios: list[E2EScenario]) -> list[str]:
    component_text = {_normalize(str(case["text"])) for case in build_cases() if case.get("text")}
    component_text.update(
        _normalize(str(case["query"])) for case in build_retrieval_cases() if case.get("query")
    )
    return [scenario.id for scenario in scenarios if _normalize(scenario.message) in component_text]


def _normalize(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold(), flags=re.UNICODE)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


if __name__ == "__main__":
    main()
