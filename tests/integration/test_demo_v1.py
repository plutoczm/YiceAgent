"""24 logical P6 cases: real HTTP/PG plus explicit reused boundary regressions.

Cases share one disposable schema in manifest order; prerequisite failures fail
dependent cases rather than skipping them. Never run against the main demo DB.
"""

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from p4_helpers import ROOT, config, oracle, query_pinned, run, select

sys.path.insert(0, str(ROOT / "scripts"))
from demo_process import stop_owned
from demo_runtime import STATE, down, health
from dev import SERVICES, probe_service, service_process
from verify_import import verify as verify_import

MANIFEST = json.loads((ROOT / "demo/scenarios/e2e-v1.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def context():
    settings, directory = config()
    assert settings["mode"] == "offline"
    evidence = []
    with httpx2.Client(
        base_url="http://127.0.0.1:8010", trust_env=False, timeout=60
    ) as client:
        state = {
            "client": client,
            "settings": settings,
            "directory": directory,
            "evidence": evidence,
            "conversation": "p6-" + uuid4().hex,
            "cases": [],
        }
        yield state
    output = Path(os.environ["WORKFLOWOPS_P6_EVIDENCE"])
    output.write_text(
        json.dumps({"cases": state["cases"], "runs": evidence}, indent=2) + "\n",
        encoding="utf-8",
    )


def invoke(c, workflow, payload):
    return run(c["client"], workflow, payload, c["evidence"])


def regression(service, *nodes):
    assert (
        service_process(
            service, ["-m", "pytest", *nodes, "-q", "-rs", "-o", "addopts="]
        ).returncode
        == 0
    )


def prepare(c, order, suffix):
    return invoke(
        c,
        "support_message",
        {
            "action": "refund",
            "order_id": order,
            "conversation_id": c["conversation"] + suffix,
        },
    )


def lookup(c, operation):
    return invoke(c, "support_lookup", {"kind": "operation", "object_id": operation})[
        "result"
    ]


def report(c, name):
    result = invoke(c, "analytics_report", {"report_id": name})
    assert result["status"] == "completed"
    return result["result"]


def e01(c, tmp):
    verify_import()
    assert all(probe_service(s)["passed"] for s in SERVICES)
    assert all(health(p) for p in (8010, 8011, 8012))


def e02(c, tmp):
    for name in ("first", "second"):
        assert (
            service_process(
                "datacopilot",
                [
                    "scripts/generate_demo_data.py",
                    "--seed",
                    "42",
                    "--output",
                    str(tmp / name),
                ],
            ).returncode
            == 0
        )
    a = (tmp / "first/fixture.json").read_bytes()
    assert a == (tmp / "second/fixture.json").read_bytes()
    assert a == (ROOT / "demo/expected/fixture-v1.json").read_bytes()
    orders = json.loads(a)["orders"]
    assert len(orders) == 24 and len({o["tenant_id"] for o in orders}) == 2
    c["fixture_hash"] = hashlib.sha256(a).hexdigest()


def e03(c, tmp):
    regression("supportops", "tests/test_demo_data.py")


def e04(c, tmp):
    select(c["client"], "customer-a1")
    result = invoke(c, "support_lookup", {"object_id": "ORD-1001"})
    assert result["status"] == "completed" and result["result"]["amount"] == 199
    c["private_run"] = result["run_id"]


def e05(c, tmp):
    denied = invoke(c, "support_lookup", {"object_id": "ORD-1005"})
    assert denied["status"] == "failed" and denied["result"] is None
    assert "amount" not in json.dumps(denied)


def e06(c, tmp):
    denied = invoke(c, "support_lookup", {"object_id": "ORD-2001"})
    assert denied["status"] == "failed" and denied["result"] is None
    select(c["client"], "customer-b1")
    assert invoke(c, "support_lookup", {"object_id": "ORD-1001"})["result"] is None
    select(c["client"], "customer-a1")


def e07(c, tmp):
    result = invoke(
        c,
        "support_message",
        {"conversation_id": c["conversation"], "message": "refund policy"},
    )["result"]
    assert result["citations"] and result["citations"][0]["source_uri"]
    regression(
        "supportops",
        "tests/test_p3_demo.py::test_offline_constructs_no_model_or_embedding",
    )


def e08(c, tmp):
    result = invoke(
        c,
        "support_message",
        {
            "conversation_id": c["conversation"] + "unknown",
            "message": "zzzzqxyunrelated",
        },
    )["result"]
    assert not result["citations"] and result["handoff"]


def e09(c, tmp):
    low = prepare(c, "ORD-1001", "low")
    assert low["status"] == "waiting_confirmation"
    c["low"] = low["domain_refs"]["operation_id"]
    assert lookup(c, c["low"])["status"] == "pending_confirmation"
    assert (
        invoke(c, "support_lookup", {"object_id": "ORD-1001"})["result"][
            "refund_status"
        ]
        == "none"
    )


def e10(c, tmp):
    result = invoke(c, "support_confirm", {"action_id": c["low"], "confirm": True})
    assert (
        result["status"] == "completed"
        and result["result"]["operation_status"] == "submitted"
    )
    c["low_version"] = lookup(c, c["low"])["version"]
    assert (
        invoke(c, "support_lookup", {"object_id": "ORD-1001"})["result"][
            "refund_status"
        ]
        == "requested"
    )


def e11(c, tmp):
    assert (
        invoke(c, "support_confirm", {"action_id": c["low"], "confirm": True})[
            "result"
        ]["operation_status"]
        == "submitted"
    )
    assert lookup(c, c["low"])["version"] == c["low_version"]


def e12(c, tmp):
    high = prepare(c, "ORD-1002", "high")
    assert high["status"] == "waiting_approval"
    c["high"] = high["domain_refs"]["operation_id"]
    op = lookup(c, c["high"])
    assert op["status"] == "pending_approval"
    denied = invoke(
        c,
        "support_review",
        {
            "operation_id": c["high"],
            "decision": "approve",
            "expected_version": op["version"],
        },
    )
    assert denied["error"] == "authorization_denied"
    assert (
        invoke(c, "support_lookup", {"object_id": "ORD-1002"})["result"][
            "refund_status"
        ]
        == "none"
    )


def e13(c, tmp):
    op = lookup(c, c["high"])
    select(c["client"], "operator-a")
    approved = invoke(
        c,
        "support_review",
        {
            "operation_id": c["high"],
            "decision": "approve",
            "expected_version": op["version"],
        },
    )
    assert approved["result"]["status"] == "pending_confirmation"
    select(c["client"], "customer-a1")
    assert (
        invoke(c, "support_lookup", {"object_id": "ORD-1002"})["result"][
            "refund_status"
        ]
        == "none"
    )
    assert (
        invoke(c, "support_confirm", {"action_id": c["high"], "confirm": True})[
            "result"
        ]["operation_status"]
        == "submitted"
    )


def e14(c, tmp):
    cancelled = prepare(c, "ORD-1004", "cancel")
    action = cancelled["domain_refs"]["operation_id"]
    invoke(c, "support_confirm", {"action_id": action, "confirm": False})
    assert lookup(c, action)["status"] == "cancelled"
    invoke(c, "support_confirm", {"action_id": action, "confirm": True})
    assert lookup(c, action)["status"] == "cancelled"
    regression(
        "supportops",
        "tests/test_business_operations.py::test_terminal_replay_and_new_attempt_require_fresh_authority",
        "tests/test_business_operations.py::test_authorization_drift_is_durable_and_old_operation_never_revives",
        "tests/test_business_operations.py::test_approval_never_authorizes_changed_snapshot",
    )


def e15(c, tmp):
    human = invoke(
        c,
        "support_message",
        {
            "action": "handoff",
            "conversation_id": c["conversation"] + "human",
            "message": "P6 private consultation",
        },
    )
    c["ticket"] = human["domain_refs"]["ticket_id"]
    select(c["client"], "operator-a")
    assert (
        invoke(c, "ticket_action", {"action": "assign", "ticket_id": c["ticket"]})[
            "result"
        ]["assignee_id"]
        == "operator-a"
    )
    invoke(
        c,
        "ticket_action",
        {
            "action": "reply",
            "ticket_id": c["ticket"],
            "message": "P6 private operator reply",
        },
    )
    select(c["client"], "customer-a1")
    invoke(
        c,
        "ticket_action",
        {
            "action": "reply",
            "ticket_id": c["ticket"],
            "message": "P6 private customer reply",
        },
    )
    messages = invoke(
        c, "ticket_action", {"action": "messages", "ticket_id": c["ticket"]}
    )["result"]
    assert [m["sender_role"] for m in messages] == ["agent", "customer"]
    assert [m["body"] for m in messages] == [
        "P6 private operator reply",
        "P6 private customer reply",
    ]


def e16(c, tmp):
    for workflow, payload in [
        ("analytics_report", {"report_id": "orders_total"}),
        ("refresh_snapshot", {}),
        ("ticket_action", {"action": "assign", "ticket_id": c["ticket"]}),
    ]:
        assert invoke(c, workflow, payload)["error"] == "authorization_denied"
    select(c["client"], "customer-a2")
    assert (
        invoke(c, "ticket_action", {"action": "get", "ticket_id": c["ticket"]})[
            "result"
        ]
        is None
    )
    assert c["client"].get("/api/runs/" + c["private_run"]).status_code == 404


def e17(c, tmp):
    select(c["client"], "operator-a")
    assert invoke(c, "refresh_snapshot", {})["status"] == "completed"
    c["baseline"] = report(c, "tickets_by_status")
    c["index"] = Path(c["settings"]["delivery_root"]) / "tenant-a/current.json"
    c["old_index"] = json.loads(c["index"].read_bytes())
    c["old_path"] = c["index"].parent / c["old_index"]["export_id"]
    c["old_bytes"] = {p.name: p.read_bytes() for p in c["old_path"].iterdir()}
    assert report(c, "orders_total")["data"]["rows"] == [[12]]


def e18(c, tmp):
    select(c["client"], "customer-a1")
    invoke(
        c,
        "support_message",
        {
            "action": "handoff",
            "conversation_id": c["conversation"] + "new",
            "message": "P6 later consultation",
        },
    )
    select(c["client"], "operator-a")
    assert (
        report(c, "tickets_by_status")["data"]["rows"] == c["baseline"]["data"]["rows"]
    )
    assert invoke(c, "refresh_snapshot", {})["status"] == "completed"
    fresh = report(c, "tickets_by_status")
    assert fresh["data"]["rows"] != c["baseline"]["data"]["rows"]
    assert (
        fresh["data"]["snapshot"]["snapshot_id"]
        != c["baseline"]["data"]["snapshot"]["snapshot_id"]
    )
    assert c["old_bytes"] == {p.name: p.read_bytes() for p in c["old_path"].iterdir()}
    old = query_pinned(c["settings"], c["old_index"]["export_id"])
    assert (
        old["snapshot"]["snapshot_id"]
        == c["baseline"]["data"]["snapshot"]["snapshot_id"]
    )


def e19(c, tmp):
    for name in MANIFEST["reports"]:
        view = report(c, name)
        expected = oracle(view["as_of"])[name]
        assert view["data"]["rows"] == expected and view["data"]["row_count"] == len(
            expected
        )
        assert view["source"] == "template" and view["template_version"] == "v1"
        assert (
            view["data"]["snapshot"]["consistency"]
            == "postgresql_repeatable_read_read_only"
        )


def e20(c, tmp):
    # Existing real HTTP mutations include hash/schema/tenant/path/fingerprint.
    from test_p4_delivery import (
        test_corrupt_delivery_fails_and_old_snapshot_remains_queryable,
    )

    for mutation in ("hash", "tenant", "schema", "path", "fingerprint"):
        test_corrupt_delivery_fails_and_old_snapshot_remains_queryable(mutation)


def e21(c, tmp):
    regression(
        "datacopilot",
        "tests/test_p4_reports.py::test_template_candidate_is_untrusted",
        "tests/infrastructure/business_analytics/test_duckdb_executor.py::test_result_budgets_fail_closed_without_returning_partial_rows",
        "tests/infrastructure/business_analytics/test_duckdb_executor.py::test_oversized_cells_and_column_counts_are_rejected",
        "tests/infrastructure/business_analytics/test_duckdb_executor.py::test_duckdb_sandbox_disables_external_access_and_locks_settings",
    )
    response = c["client"].post(
        "/api/workflows/analytics_report/runs",
        json={"payload": {"report_id": "orders_total", "sql": "DROP TABLE orders"}},
    )
    assert response.status_code == 422


def e22(c, tmp):
    before = service_process(
        "supportops", ["scripts/p6_database_state.py", "--test-schema"], capture=True
    )
    assert before.returncode == 0
    marker = c["directory"] / "supportops-process.json"
    stop_owned(json.loads(marker.read_text(encoding="utf-8")))
    select(c["client"], "customer-a1")
    assert invoke(c, "support_lookup", {"object_id": "ORD-1001"})["status"] == "failed"
    unknown = invoke(c, "support_confirm", {"action_id": c["low"], "confirm": True})
    assert (
        unknown["status"] == "unknown_outcome"
        and unknown["domain_refs"]["operation_id"] == c["low"]
    )
    regression(
        "harness",
        "backend/tests/test_p3_workflow.py::test_unknown_write_has_operation_and_no_retry",
    )
    after = service_process(
        "supportops", ["scripts/p6_database_state.py", "--test-schema"], capture=True
    )
    assert after.returncode == 0
    assert json.loads(before.stdout.strip().splitlines()[-1]) == json.loads(
        after.stdout.strip().splitlines()[-1]
    )


def e23(c, tmp):
    texts = []
    for item in c["evidence"]:
        path = ROOT / ".runtime/runs" / (item["run_id"] + ".jsonl")
        text = path.read_text(encoding="utf-8")
        texts.append(text)
        assert "steps" in text and item["status"] in text
    logs = "".join(texts)
    for secret in (
        *c["settings"]["supportops_tokens"].values(),
        *c["settings"]["datacopilot_tokens"].values(),
        c["conversation"],
        "P6 private consultation",
        "P6 private operator reply",
        "P6 private customer reply",
        '"rows"',
        "SELECT ",
    ):
        assert secret not in logs
    select(c["client"], "customer-a2")
    assert c["client"].get("/api/runs/" + c["private_run"]).status_code == 404
    select(c["client"], "customer-b1")
    assert c["client"].get("/api/runs/" + c["private_run"]).status_code == 404


def e24(c, tmp):
    for name in ("DEMO.md", "INTERVIEW.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "NOT_RUN" in text and "Float" in text and "BM25" in text
    html = c["client"].get("/").text
    assert "offline" in html and "模拟" in html
    regression(
        "supportops",
        "tests/test_p3_demo.py::test_offline_constructs_no_model_or_embedding",
    )
    from demo_db import CONTAINER, inspect_resource

    assert (
        inspect_resource("container", CONTAINER)["State"]["Health"]["Status"]
        == "healthy"
    )
    c["client"].close()
    down()
    assert not STATE.exists() and all(not health(p) for p in (8010, 8011, 8012))
    assert (
        inspect_resource("container", CONTAINER)["State"]["Health"]["Status"]
        == "healthy"
    )


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["id"])
def test_demo_v1(case, context, tmp_path):
    """Execute each fixed logical case; append only after its assertions pass."""
    before = len(context["evidence"])
    globals()[case["id"].lower()](context, tmp_path)
    context["cases"].append(
        {
            "id": case["id"],
            "status": "passed",
            "run_ids": [r["run_id"] for r in context["evidence"][before:]],
            "at": datetime.now(UTC).isoformat(),
        }
    )
