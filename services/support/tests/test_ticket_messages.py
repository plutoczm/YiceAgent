from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build_container
from app.main import create_app

CUSTOMER_HEADERS = {
    "X-Principal-Id": "customer-1",
    "X-Customer-Id": "CUST-001",
    "X-Roles": "customer",
}
OTHER_CUSTOMER_HEADERS = {
    "X-Principal-Id": "customer-2",
    "X-Customer-Id": "CUST-002",
    "X-Roles": "customer",
}
AGENT_HEADERS = {
    "X-Principal-Id": "agent-1",
    "X-Roles": "support_agent",
}


def build_client(tmp_path) -> TestClient:
    container = build_container(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'ticket_messages.db'}",
            metrics_enabled=False,
            tracing_enabled=False,
            app_env="development",
            auth_mode="dev",
            mcp_auth_mode="disabled",
            seed_demo_data=True,
        )
    )
    return TestClient(create_app(container))


def create_handoff(client: TestClient) -> str:
    response = client.post(
        "/v1/support/messages",
        headers=CUSTOMER_HEADERS,
        json={"conversation_id": "CONV-HANDOFF", "message": "我要投诉服务态度"},
    )
    assert response.status_code == 200
    ticket_id = response.json()["ticket_id"]
    assert ticket_id
    return ticket_id


def test_ticket_messages_are_visible_to_customer_and_assigned_agent(tmp_path):
    client = build_client(tmp_path)
    ticket_id = create_handoff(client)

    initial = client.get(f"/v1/tickets/{ticket_id}/messages", headers=CUSTOMER_HEADERS)
    assert initial.status_code == 200
    assert [message["sender_role"] for message in initial.json()] == ["customer", "assistant"]

    recovered = client.get("/v1/conversations/CONV-HANDOFF/ticket", headers=CUSTOMER_HEADERS)
    assert recovered.status_code == 200
    assert recovered.json()["ticket_id"] == ticket_id

    unassigned_reply = client.post(
        f"/v1/agent/tickets/{ticket_id}/messages",
        headers=AGENT_HEADERS,
        json={"message": "我来协助处理。"},
    )
    assert unassigned_reply.status_code == 409
    assert unassigned_reply.json()["detail"] == "ticket_not_assigned"

    assigned = client.post(
        f"/v1/agent/tickets/{ticket_id}/assign",
        headers=AGENT_HEADERS,
        json={"assignee_id": "agent-1"},
    )
    assert assigned.status_code == 200

    agent_reply = client.post(
        f"/v1/agent/tickets/{ticket_id}/messages",
        headers=AGENT_HEADERS,
        json={"message": "我已收到，会尽快为你核验。"},
    )
    assert agent_reply.status_code == 201
    assert agent_reply.json()["sender_role"] == "agent"

    customer_history = client.get(f"/v1/tickets/{ticket_id}/messages", headers=CUSTOMER_HEADERS)
    assert customer_history.status_code == 200
    assert [message["sender_role"] for message in customer_history.json()] == [
        "customer",
        "assistant",
        "agent",
    ]

    customer_reply = client.post(
        f"/v1/tickets/{ticket_id}/messages",
        headers=CUSTOMER_HEADERS,
        json={"message": "好的，请告诉我后续进展。"},
    )
    assert customer_reply.status_code == 201
    assert customer_reply.json()["sender_role"] == "customer"

    ticket = client.get(f"/v1/tickets/{ticket_id}", headers=CUSTOMER_HEADERS)
    assert ticket.status_code == 200
    assert ticket.json()["status"] == "assigned"

    agent_history = client.get(
        f"/v1/agent/tickets/{ticket_id}/messages",
        headers=AGENT_HEADERS,
    )
    assert agent_history.status_code == 200
    assert [message["sender_role"] for message in agent_history.json()] == [
        "customer",
        "assistant",
        "agent",
        "customer",
    ]


def test_customer_cannot_read_or_write_another_customers_ticket_messages(tmp_path):
    client = build_client(tmp_path)
    ticket_id = create_handoff(client)

    read_other = client.get(f"/v1/tickets/{ticket_id}/messages", headers=OTHER_CUSTOMER_HEADERS)
    recover_other = client.get(
        "/v1/conversations/CONV-HANDOFF/ticket",
        headers=OTHER_CUSTOMER_HEADERS,
    )
    write_other = client.post(
        f"/v1/tickets/{ticket_id}/messages",
        headers=OTHER_CUSTOMER_HEADERS,
        json={"message": "not allowed"},
    )

    assert read_other.status_code == 404
    assert recover_other.status_code == 404
    assert write_other.status_code == 404
