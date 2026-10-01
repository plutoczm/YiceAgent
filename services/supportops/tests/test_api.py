from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build_container
from app.main import create_app

CUSTOMER_HEADERS = {
    "X-Principal-Id": "user-1",
    "X-Customer-Id": "CUST-001",
    "X-Roles": "customer",
}
AGENT_HEADERS = {
    "X-Principal-Id": "agent-1",
    "X-Roles": "support_agent",
}


def build_client(tmp_path, name: str = "api.db"):
    container = build_container(
        Settings(
            database_url=f"sqlite:///{tmp_path / name}",
            app_env="development",
            auth_mode="dev",
            mcp_auth_mode="disabled",
            seed_demo_data=True,
        )
    )
    return container, TestClient(create_app(container))


def test_api_requires_identity(tmp_path):
    _, client = build_client(tmp_path)
    response = client.post(
        "/v1/support/messages",
        json={"conversation_id": "CONV-AUTH", "message": "订单状态 ORD-1001"},
    )
    assert response.status_code == 401


def test_api_refund_confirmation_flow(tmp_path):
    _, client = build_client(tmp_path)
    response = client.post(
        "/v1/support/messages",
        headers=CUSTOMER_HEADERS,
        json={"conversation_id": "CONV-API", "message": "退款 ORD-1001"},
    )
    assert response.status_code == 200
    action_id = response.json()["pending_action_id"]
    assert action_id

    confirm = client.post(
        f"/v1/actions/{action_id}/confirm",
        headers=CUSTOMER_HEADERS,
        json={"confirm": True},
    )
    assert confirm.status_code == 200
    assert confirm.json()["result"]["refund_status"] == "requested"


def test_api_prevents_cross_customer_order_read(tmp_path):
    _, client = build_client(tmp_path, "api2.db")
    response = client.get("/v1/orders/ORD-2001", headers=CUSTOMER_HEADERS)
    assert response.status_code == 404


def test_customer_cannot_use_agent_workspace(tmp_path):
    _, client = build_client(tmp_path, "api3.db")
    response = client.get("/v1/agent/tickets", headers=CUSTOMER_HEADERS)
    assert response.status_code == 403


def test_agent_can_assign_and_resolve_ticket(tmp_path):
    _, client = build_client(tmp_path, "api4.db")
    complaint = client.post(
        "/v1/support/messages",
        headers=CUSTOMER_HEADERS,
        json={"conversation_id": "CONV-COMPLAINT", "message": "我要投诉服务态度"},
    )
    ticket_id = complaint.json()["ticket_id"]
    assert ticket_id

    listed = client.get("/v1/agent/tickets", headers=AGENT_HEADERS)
    assert listed.status_code == 200
    assert any(item["ticket_id"] == ticket_id for item in listed.json())

    assigned = client.post(
        f"/v1/agent/tickets/{ticket_id}/assign",
        headers=AGENT_HEADERS,
        json={"assignee_id": "agent-1"},
    )
    assert assigned.status_code == 200
    assert assigned.json()["status"] == "assigned"

    resolved = client.post(
        f"/v1/agent/tickets/{ticket_id}/transition",
        headers=AGENT_HEADERS,
        json={"status": "resolved", "note": "handled"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
