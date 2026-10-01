"""Real localhost HTTP against the P2-owned PostgreSQL; no transport mocks."""

import json
from pathlib import Path
from uuid import uuid4

import httpx2

ROOT = Path(__file__).resolve().parents[2]


def test_real_support_loop():
    config_path = Path(
        json.loads((ROOT / ".runtime/demo-processes.json").read_text())["config"]
    )
    config = json.loads(config_path.read_text())
    assert config.get("test_schema", "").startswith("p3_test_"), (
        "Use scripts/dev.py test-integration --group support to provision isolated HTTP resources"
    )
    evidence = []
    with httpx2.Client(
        base_url="http://127.0.0.1:8010", trust_env=False, timeout=30
    ) as client:

        def select(persona):
            assert (
                client.post("/api/demo/session", json={"persona": persona}).status_code
                == 200
            )

        def run(workflow, payload, parent=None):
            body = {"payload": payload}
            if parent:
                body["parent_run_id"] = parent
            response = client.post(f"/api/workflows/{workflow}/runs", json=body)
            assert response.status_code == 200
            result = response.json()
            evidence.append(
                {
                    k: result[k]
                    for k in ("run_id", "workflow_id", "status", "domain_refs")
                }
            )
            return result

        select("customer-a1")
        order = run("support_lookup", {"object_id": "ORD-1001"})
        assert order["result"]["amount"] == 199
        private_run = order["run_id"]
        for wrong in ("ORD-1005", "ORD-2001"):
            denied = run("support_lookup", {"object_id": wrong})
            assert denied["status"] == "failed" and denied["result"] is None
        conversation = "p3-" + uuid4().hex
        low = run(
            "support_message",
            {
                "action": "refund",
                "conversation_id": conversation,
                "order_id": "ORD-1001",
            },
        )
        assert low["status"] == "waiting_confirmation"
        operation_id = low["domain_refs"]["operation_id"]
        before = run("support_lookup", {"kind": "operation", "object_id": operation_id})
        assert before["result"]["status"] == "pending_confirmation"
        action_id = low["domain_refs"]["pending_action_id"]
        confirmed = run("support_confirm", {"action_id": action_id, "confirm": True})
        assert confirmed["domain_refs"]["operation_id"] == operation_id == action_id
        assert (
            confirmed["status"] == "completed"
            and confirmed["result"]["operation_status"] == "submitted"
        )
        submitted = run(
            "support_lookup", {"kind": "operation", "object_id": operation_id}
        )
        repeated = run(
            "support_confirm", {"action_id": action_id, "confirm": True}, low["run_id"]
        )
        assert (
            repeated["result"] == confirmed["result"]
            or repeated["result"]["operation_status"] == "submitted"
        )
        after = run("support_lookup", {"kind": "operation", "object_id": operation_id})
        assert after["result"]["status"] == "submitted"
        assert after["result"]["version"] == submitted["result"]["version"]

        high = run(
            "support_message",
            {
                "action": "refund",
                "conversation_id": conversation + "h",
                "order_id": "ORD-1002",
            },
        )
        assert high["status"] == "waiting_approval"
        high_id = high["domain_refs"]["operation_id"]
        version = run("support_lookup", {"kind": "operation", "object_id": high_id})[
            "result"
        ]["version"]
        denied = run(
            "support_review",
            {
                "operation_id": high_id,
                "decision": "approve",
                "expected_version": version,
            },
        )
        assert (
            denied["status"] == "failed" and denied["error"] == "authorization_denied"
        )
        unchanged = run("support_lookup", {"kind": "operation", "object_id": high_id})[
            "result"
        ]
        assert (
            unchanged["status"] == "pending_approval"
            and unchanged["version"] == version
        )

        human = run(
            "support_message",
            {
                "action": "handoff",
                "conversation_id": conversation + "human",
                "message": "P3 human consultation",
            },
        )
        ticket = human["domain_refs"]["ticket_id"]
        select("operator-a")
        assigned = run("ticket_action", {"action": "assign", "ticket_id": ticket})
        assert assigned["result"]["assignee_id"] == "operator-a"
        replied = run(
            "ticket_action",
            {
                "action": "reply",
                "ticket_id": ticket,
                "message": "P3 real operator reply",
            },
            human["run_id"],
        )
        assert replied["result"]["body"] == "P3 real operator reply"
        review = run(
            "support_review",
            {
                "operation_id": high_id,
                "decision": "approve",
                "expected_version": version,
            },
            high["run_id"],
        )
        assert (
            review["status"] == "waiting_confirmation"
            and review["result"]["status"] == "pending_confirmation"
        )
        select("customer-a1")
        prepared = run(
            "support_message",
            {
                "action": "refund",
                "conversation_id": conversation + "h",
                "order_id": "ORD-1002",
            },
        )
        assert prepared["status"] == "waiting_confirmation"
        final = run(
            "support_confirm",
            {
                "action_id": prepared["domain_refs"]["pending_action_id"],
                "confirm": True,
            },
            prepared["run_id"],
        )
        assert final["result"]["operation_status"] == "submitted"
        customer_reply = run(
            "ticket_action",
            {
                "action": "reply",
                "ticket_id": ticket,
                "message": "P3 real customer reply",
            },
            human["run_id"],
        )
        assert customer_reply["result"]["body"] == "P3 real customer reply"
        messages = run("ticket_action", {"action": "messages", "ticket_id": ticket})[
            "result"
        ]
        assert [row["sender_role"] for row in messages] == ["agent", "customer"]
        assert (
            run("ticket_action", {"action": "get", "ticket_id": ticket})["result"][
                "status"
            ]
            == "assigned"
        )
        knowledge = run(
            "support_message",
            {"conversation_id": conversation + "kb", "message": "refund policy"},
        )
        assert (
            knowledge["result"]["citations"]
            and knowledge["result"]["citations"][0]["source_uri"]
        )
        select("customer-a2")
        assert client.get("/api/runs/" + private_run).status_code == 404
        assert (
            run("ticket_action", {"action": "get", "ticket_id": ticket})["status"]
            == "failed"
        )
        select("customer-b1")
        assert client.get("/api/runs/" + private_run).status_code == 404
        assert (
            run("ticket_action", {"action": "get", "ticket_id": ticket})["status"]
            == "failed"
        )
        assert (
            run("support_lookup", {"kind": "operation", "object_id": operation_id})[
                "status"
            ]
            == "failed"
        )
    with httpx2.Client(base_url="http://127.0.0.1:8011", trust_env=False) as domain:
        assert (
            domain.get(
                "/v1/agent/tickets",
                headers={
                    "Authorization": "Bearer "
                    + client.cookies.get("workflowops_session")
                },
            ).status_code
            == 401
        )
        assert (
            domain.get(
                "/v1/agent/tickets", headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        assert (
            domain.get(
                "/v1/agent/tickets",
                headers={
                    "Authorization": "Bearer "
                    + config["supportops_tokens"]["customer-a1"],
                    "X-Roles": "support_admin",
                },
            ).status_code
            == 403
        )
    log_text = "".join(
        path.read_text() for path in (ROOT / ".runtime/runs").glob("*.jsonl")
    )
    for secret in (
        *config["supportops_tokens"].values(),
        "P3 real operator reply",
        "P3 real customer reply",
        conversation,
    ):
        assert secret not in log_text
    (ROOT / ".runtime/p3-http-evidence.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )
