"""Real PostgreSQL behind HTTP, session isolation and uncertainty behavior."""

import socket
import threading
import time
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from workflowops.app import create_app
from workflowops.business.models import Ticket

pytestmark = pytest.mark.integration


def login(client, persona):
    response = client.post("/api/v2/demo/session", json={"persona": persona})
    assert response.status_code == 200
    client.headers["X-WorkflowOps-CSRF"] = response.json()["csrf_token"]
    return response


def request(**values):
    return {"client_request_id": str(uuid4()), **values}


def test_cookie_csrf_closed_payload_and_switch_acl(business):
    service, _ = business
    with TestClient(create_app(service)) as client:
        assert client.get("/api/v2/orders").status_code == 401
        response = login(client, "customer-a1")
        assert (
            "HttpOnly" in response.headers["set-cookie"]
            and "SameSite=strict" in response.headers["set-cookie"]
        )
        old = client.cookies.get("workflowops_v2_session")
        assert client.get("/api/v2/orders/ECO-A-01").status_code == 200
        assert client.get("/api/v2/orders/ECO-A-02").status_code == 404
        assert client.get("/api/v2/orders/ECO-B-01").status_code == 404
        bad = request(
            kind="create_ticket", order_id=None, message="help", tenant_id="tenant-b", approved=True
        )
        assert client.post("/api/v2/actions", json=bad).status_code == 422
        assert (
            client.post(
                "/api/v2/actions",
                json=request(kind="create_ticket", order_id=None, message="help"),
                headers={"X-WorkflowOps-CSRF": ""},
            ).status_code
            == 403
        )
        login(client, "customer-b1")
        assert client.get("/api/v2/orders/ECO-A-01").status_code == 404
        assert client.get("/api/v2/orders/ECO-B-01").status_code == 200
        with TestClient(client.app) as previous:
            previous.cookies.set("workflowops_v2_session", old)
            assert previous.get("/api/v2/orders").status_code == 401


def test_http_unknown_outcome_can_be_reconciled_without_replay(business):
    service, _ = business
    with TestClient(create_app(service)) as client:
        login(client, "customer-a1")
        draft = client.post(
            "/api/v2/actions", json=request(kind="create_ticket", order_id=None, message="需要协助")
        ).json()
        command = request(confirm=True, expected_version=draft["version"])

        def lost(stage):
            if stage == "after_commit":
                raise RuntimeError("PRIVATE_DATABASE_SECRET")

        service.fault_hook = lost
        response = client.post("/api/v2/actions/" + draft["action_id"] + "/confirm", json=command)
        assert response.status_code == 503 and response.json()["status"] == "unknown_outcome"
        assert "PRIVATE_DATABASE_SECRET" not in response.text
        service.fault_hook = None
        result = client.get("/api/v2/actions/requests/" + command["client_request_id"])
        assert result.status_code == 200 and result.json()["committed"]
        with Session(service.engine) as session:
            assert session.scalar(select(func.count()).select_from(Ticket)) == 1
        login(client, "customer-b1")
        assert (
            client.get("/api/v2/actions/requests/" + command["client_request_id"]).status_code
            == 404
        )


def test_actual_loopback_http_order_payment_cancellation_and_ticket(business):
    service, _ = business
    app = create_app(service)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", access_log=False, loop="asyncio"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    try:
        with (
            httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False
            ) as customer,
            httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False
            ) as operator,
        ):
            login(customer, "customer-a1")
            login(operator, "operator-a")

            def confirm(client, row):
                response = client.post(
                    "/api/v2/actions/" + row["action_id"] + "/confirm",
                    json=request(confirm=True, expected_version=row["version"]),
                )
                assert response.status_code == 200
                return response.json()

            for kind, sku in [("ecommerce", "ECO-A-CABLE"), ("food", "FOOD-A-TEA")]:
                result = customer.post(
                    "/api/v2/orders/drafts",
                    json=request(
                        kind="place_order",
                        order_type=kind,
                        items=[{"product_id": sku, "quantity": 2}],
                        note="",
                    ),
                )
                assert result.status_code == 200
                order_id = confirm(customer, result.json())["result"]["order_id"]
                row = customer.get("/api/v2/orders/" + order_id).json()
                assert row["amount"] == "39.80" and row["payment_status"] == "unpaid"
                result = customer.post(
                    "/api/v2/actions", json=request(kind="pay", order_id=order_id)
                ).json()
                confirm(customer, result)
                row = operator.get("/api/v2/orders/" + order_id).json()
                assert row["payment_status"] == "pending"
                response = operator.post(
                    "/api/v2/simulator/events",
                    json=request(
                        order_id=order_id,
                        expected_version=row["version"],
                        event="payment_succeeded",
                    ),
                )
                assert response.status_code == 200 and response.json()["payment_status"] == "paid"
                result = customer.post(
                    "/api/v2/actions", json=request(kind="cancel_order", order_id=order_id)
                ).json()
                confirm(customer, result)
                row = operator.get("/api/v2/orders/" + order_id).json()
                assert row["status"] == "cancelled" and row["refunded_amount"] == "0.00"
                response = operator.post(
                    "/api/v2/simulator/events",
                    json=request(
                        order_id=order_id, expected_version=row["version"], event="refund_succeeded"
                    ),
                )
                assert response.status_code == 200 and response.json()["refunded_amount"] == "39.80"
                draft = customer.post(
                    "/api/v2/actions",
                    json=request(
                        kind="create_ticket", order_id=order_id, message="请核对模拟取消结果"
                    ),
                ).json()
                ticket_id = confirm(customer, draft)["result"]["ticket_id"]
                draft = operator.post(
                    "/api/v2/actions",
                    json=request(kind="reply_ticket", ticket_id=ticket_id, message="已核对"),
                ).json()
                confirm(operator, draft)
                assert len(customer.get("/api/v2/tickets/" + ticket_id + "/messages").json()) == 2
                assert (
                    operator.post(
                        "/api/v2/simulator/events",
                        json=request(order_id=order_id, expected_version=1, event="advance"),
                    ).status_code
                    == 409
                )
    finally:
        server.should_exit = True
        thread.join(10)
        assert not thread.is_alive()
