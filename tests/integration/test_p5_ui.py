"""Real HTTP bindings behind the four-view workbench, with PostgreSQL facts."""

import json
from uuid import uuid4

import httpx2
from p4_helpers import ROOT, config, oracle, run, select


def test_workbench_real_actions():
    configuration, directory = config()
    evidence = []
    conversation = "p5-" + uuid4().hex
    with httpx2.Client(
        base_url="http://127.0.0.1:8010", trust_env=False, timeout=60
    ) as client:
        assert client.get("/").status_code == 200
        assert "明确确认提交申请" in client.get("/").text
        assert client.get("/workbench/app.js").status_code == 200
        select(client, "operator-a")
        run(client, "refresh_snapshot", {}, evidence)
        old = run(
            client,
            "analytics_report",
            {"report_id": "refund_requests_by_status"},
            evidence,
        )
        assert old["result"]["data"]["rows"] == []
        select(client, "customer-a1")
        order = run(client, "support_lookup", {"object_id": "ORD-1001"}, evidence)
        assert order["result"]["amount"] == 199
        private_run = order["run_id"]
        knowledge = run(
            client,
            "support_message",
            {"conversation_id": conversation, "message": "refund policy"},
            evidence,
        )
        assert knowledge["result"]["citations"]
        low = run(
            client,
            "support_message",
            {
                "action": "refund",
                "conversation_id": conversation,
                "order_id": "ORD-1001",
            },
            evidence,
        )
        assert low["status"] == "waiting_confirmation"
        operation = low["domain_refs"]["operation_id"]
        assert (
            run(client, "support_lookup", {"object_id": "ORD-1001"}, evidence)[
                "result"
            ]["refund_status"]
            == "none"
        )
        submitted = run(
            client,
            "support_confirm",
            {"action_id": operation, "confirm": True},
            evidence,
        )
        repeated = run(
            client,
            "support_confirm",
            {"action_id": operation, "confirm": True},
            evidence,
        )
        assert (
            submitted["result"]["operation_status"]
            == repeated["result"]["operation_status"]
            == "submitted"
        )
        high = run(
            client,
            "support_message",
            {
                "action": "refund",
                "conversation_id": conversation + "high",
                "order_id": "ORD-1002",
            },
            evidence,
        )
        assert high["status"] == "waiting_approval"
        high_id = high["domain_refs"]["operation_id"]
        blocked = run(client, "support_lookup", {"object_id": "ORD-1002"}, evidence)
        assert blocked["result"]["refund_status"] == "none"
        human = run(
            client,
            "support_message",
            {
                "action": "handoff",
                "conversation_id": conversation + "human",
                "message": "P5 UI human case",
            },
            evidence,
        )
        ticket = human["domain_refs"]["ticket_id"]
        select(client, "operator-a")
        stale = run(
            client,
            "analytics_report",
            {"report_id": "refund_requests_by_status"},
            evidence,
        )
        assert stale["result"]["data"]["rows"] == []
        assert (
            stale["result"]["data"]["snapshot"]["snapshot_id"]
            == old["result"]["data"]["snapshot"]["snapshot_id"]
        )
        run(
            client, "ticket_action", {"action": "assign", "ticket_id": ticket}, evidence
        )
        assert (
            run(
                client,
                "ticket_action",
                {"action": "get", "ticket_id": ticket},
                evidence,
            )["result"]["assignee_id"]
            == "operator-a"
        )
        run(
            client,
            "ticket_action",
            {
                "action": "reply",
                "ticket_id": ticket,
                "message": "P5 actual operator reply",
            },
            evidence,
        )
        op = run(
            client,
            "support_lookup",
            {"kind": "operation", "object_id": high_id},
            evidence,
        )["result"]
        reviewed = run(
            client,
            "support_review",
            {
                "operation_id": high_id,
                "decision": "approve",
                "expected_version": op["version"],
            },
            evidence,
        )
        assert reviewed["result"]["status"] == "pending_confirmation"
        select(client, "customer-a1")
        run(
            client, "support_confirm", {"action_id": high_id, "confirm": True}, evidence
        )
        run(
            client,
            "ticket_action",
            {
                "action": "reply",
                "ticket_id": ticket,
                "message": "P5 actual customer reply",
            },
            evidence,
        )
        messages = run(
            client,
            "ticket_action",
            {"action": "messages", "ticket_id": ticket},
            evidence,
        )["result"]
        assert [m["sender_role"] for m in messages] == ["agent", "customer"]
        for wrong_order in ("ORD-1005", "ORD-2001"):
            denied = run(client, "support_lookup", {"object_id": wrong_order}, evidence)
            assert denied["status"] == "failed" and denied["result"] is None
        assert (
            run(client, "analytics_report", {"report_id": "orders_total"}, evidence)[
                "error"
            ]
            == "authorization_denied"
        )
        select(client, "customer-a2")
        assert client.get("/api/runs/" + private_run).status_code == 404
        assert (
            run(
                client,
                "ticket_action",
                {"action": "get", "ticket_id": ticket},
                evidence,
            )["status"]
            == "failed"
        )
        select(client, "operator-a")
        refreshed = run(client, "refresh_snapshot", {}, evidence)
        assert refreshed["status"] == "completed"
        for report_id in (
            "orders_total",
            "refund_requests_by_status",
            "pending_approval_count",
            "tickets_by_status",
            "overdue_open_tickets",
        ):
            report = run(
                client, "analytics_report", {"report_id": report_id}, evidence
            )["result"]
            facts = oracle(report["as_of"])
            assert report["data"]["rows"] == facts[report_id]
        current = json.loads(
            (directory / "deliveries/tenant-a/current.json").read_text()
        )
        delivery = directory / "deliveries" / "tenant-a" / current["export_id"]
        data_path = delivery / "orders_v1.jsonl"
        original = data_path.read_bytes()
        try:
            data_path.write_bytes(original + b"\n")
            rejected = run(
                client, "analytics_report", {"report_id": "orders_total"}, evidence
            )
            assert rejected["status"] == "failed" and rejected["result"] is None
        finally:
            data_path.write_bytes(original)
        assert (
            run(client, "analytics_report", {"report_id": "orders_total"}, evidence)[
                "status"
            ]
            == "completed"
        )
    logs = "".join(
        (ROOT / ".runtime/runs" / (item["run_id"] + ".jsonl")).read_text()
        for item in evidence
    )
    for private in (
        *configuration["supportops_tokens"].values(),
        *configuration["datacopilot_tokens"].values(),
        conversation,
        "P5 actual operator reply",
        "P5 actual customer reply",
    ):
        assert private not in logs
    (ROOT / ".runtime/p5-http-evidence.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )
