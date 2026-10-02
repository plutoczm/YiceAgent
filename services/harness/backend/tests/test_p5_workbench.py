"""Static bindings, XSS boundaries and server-enforced workbench isolation."""

import re

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.adapters.supportops import SupportOpsAdapter
from app.demo.composition import demo_app
from app.demo.identity import PERSONAS


@pytest.fixture
def client(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.startswith("/v1/orders/"):
            return httpx2.Response(
                200,
                json={
                    "order_id": "ORD-1001",
                    "customer_id": "customer-a1",
                    "status": "delivered",
                    "amount": 199,
                    "refundable_amount": 199,
                    "refund_status": "none",
                    "return_status": "none",
                },
            )
        raise httpx2.ReadTimeout("PRIVATE_SENTINEL")

    tokens = {name: name + "-" + "s" * 40 for name in PERSONAS}
    adapter = SupportOpsAdapter(tokens, transport=httpx2.MockTransport(handler))
    with TestClient(demo_app(adapter, tmp_path)) as value:
        yield value, calls


def test_static_resources_and_closed_bindings(client):
    client, _ = client
    html = client.get("/")
    assert html.status_code == 200 and "模拟数据 / 本地演示 / offline" in html.text
    js = client.get("/workbench/app.js").text
    css = client.get("/workbench/styles.css")
    assert css.status_code == 200
    ids = set(re.findall(r'id="([^"]+)"', html.text))
    used = set(re.findall(r'\$\("([^"]+)"\)', js))
    assert used <= ids
    assert {"customer-reply-status", "operator-reply-status"} <= ids
    assert set(re.findall(r'data-view="([^"]+)"', html.text)) == {
        "customer",
        "operations",
        "analytics",
        "runs",
    }
    for workflow in (
        "support_lookup",
        "support_message",
        "support_confirm",
        "support_review",
        "ticket_action",
        "refresh_snapshot",
        "analytics_report",
    ):
        assert '"' + workflow + '"' in js
    for forbidden in (
        "innerHTML",
        "localStorage",
        "sessionStorage",
        "Authorization",
        "X-Roles",
        "http://127.0.0.1:8011",
        "http://127.0.0.1:8012",
        "/v1/",
        "setInterval",
    ):
        assert forbidden not in js
    assert "textContent" in js and "if (busy || !persona) return" in js
    assert "connect-src 'self'" in html.headers["content-security-policy"]
    assert html.headers["cache-control"] == "no-store"
    assert html.headers["x-content-type-options"] == "nosniff"
    assert client.get("/workbench/../../composition.py").status_code == 404
    assert client.get("/workbench/missing.js").status_code == 404
    assert client.get("/", headers={"Host": "external.example"}).status_code == 403


def test_customer_cannot_use_hidden_operator_actions(client):
    client, calls = client
    assert client.post("/api/demo/session", json={"persona": "customer-a1"}).status_code == 200
    for workflow, payload in (
        ("ticket_action", {"action": "list"}),
        ("ticket_action", {"action": "assign", "ticket_id": "TKT-1"}),
        ("support_review", {"operation_id": "ACT-1", "decision": "approve", "expected_version": 1}),
        ("refresh_snapshot", {}),
        ("analytics_report", {"report_id": "orders_total"}),
    ):
        result = client.post(f"/api/workflows/{workflow}/runs", json={"payload": payload}).json()
        assert result["error"] == "authorization_denied" and result["result"] is None
    assert calls == []


def test_switch_revokes_previous_cookie_and_run_access(client):
    client, _ = client
    client.post("/api/demo/session", json={"persona": "customer-a1"})
    old = client.cookies.get("workflowops_session")
    run = client.post(
        "/api/workflows/support_lookup/runs", json={"payload": {"object_id": "ORD-1001"}}
    ).json()
    client.post("/api/demo/session", json={"persona": "customer-a2"})
    assert client.get("/api/runs/" + run["run_id"]).status_code == 404
    assert old not in client.app.state.sessions.handles
    assert (
        client.post(
            "/api/demo/session",
            json={"persona": "operator-a"},
            headers={"Origin": "https://external.example"},
        ).status_code
        == 403
    )


def test_unknown_write_is_visible_and_not_retried(client):
    client, calls = client
    client.post("/api/demo/session", json={"persona": "customer-a1"})
    run = client.post(
        "/api/workflows/support_confirm/runs",
        json={"payload": {"action_id": "ACT-1", "confirm": True}},
    ).json()
    assert run["status"] == "unknown_outcome"
    assert run["domain_refs"]["operation_id"] == "ACT-1" and len(calls) == 1
    assert "PRIVATE_SENTINEL" not in str(run)
    assert client.get("/api/runs/" + run["run_id"]).json()["status"] == "unknown_outcome"


def test_operator_detail_reuses_domain_authorized_list(tmp_path):
    calls = []
    ticket = {
        "ticket_id": "TKT-1",
        "customer_id": "customer-a1",
        "conversation_id": "demo",
        "reason": "mock unit case",
        "status": "assigned",
        "priority": "normal",
        "assignee_id": "operator-a",
        "sla_due_at": "2026-10-02T00:00:00Z",
        "sla_breached": False,
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
    }

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/agent/tickets"
        return httpx2.Response(200, json=[ticket])

    tokens = {name: name + "-" + "s" * 40 for name in PERSONAS}
    adapter = SupportOpsAdapter(tokens, transport=httpx2.MockTransport(handler))
    with TestClient(demo_app(adapter, tmp_path)) as client:
        client.post("/api/demo/session", json={"persona": "operator-a"})
        for ticket_id in ("TKT-1", "TKT-hidden"):
            result = client.post(
                "/api/workflows/ticket_action/runs",
                json={"payload": {"action": "get", "ticket_id": ticket_id}},
            ).json()
            assert result["domain_refs"]["ticket_id"] == ticket_id
            if ticket_id == "TKT-1":
                assert result["result"]["assignee_id"] == "operator-a"
            else:
                assert result["status"] == "failed" and result["result"] is None
    assert len(calls) == 2


@pytest.mark.parametrize("persona,role", [("operator-a", "agent"), ("customer-a1", "customer")])
def test_acknowledged_reply_retained_when_recording_fails(tmp_path, monkeypatch, persona, role):
    calls = []
    body = "ACKNOWLEDGED_REPLY_SENTINEL"

    def handler(request):
        calls.append(request)
        return httpx2.Response(
            201,
            json={
                "message_id": "MSG-1",
                "ticket_id": "TKT-1",
                "conversation_id": "demo",
                "customer_id": "customer-a1",
                "sender_role": role,
                "sender_id": persona,
                "body": body,
                "created_at": "2026-10-02T00:00:00Z",
            },
        )

    tokens = {name: name + "-" + "s" * 40 for name in PERSONAS}
    adapter = SupportOpsAdapter(tokens, transport=httpx2.MockTransport(handler))
    with TestClient(demo_app(adapter, tmp_path)) as client:
        client.post("/api/demo/session", json={"persona": persona})
        store = client.app.state.engine.store
        original = store.append

        def fail_invocation_step(run_id, value):
            if getattr(value, "name", None) == "invoke_domain":
                raise OSError("injected record failure after reply acknowledgment")
            original(run_id, value)

        monkeypatch.setattr(store, "append", fail_invocation_step)
        result = client.post(
            "/api/workflows/ticket_action/runs",
            json={"payload": {"action": "reply", "ticket_id": "TKT-1", "message": body}},
        ).json()
        assert result["status"] == "failed" and result["error"] == "internal_error"
        assert result["result"]["message_id"] == "MSG-1"
        assert result["result"]["body"] == body and len(calls) == 1
    assert body not in "".join(p.read_text() for p in tmp_path.glob("*.jsonl"))
