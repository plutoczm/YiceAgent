from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build_container
from app.main import create_app
from app.observability import Observability

CUSTOMER_HEADERS = {
    "X-Principal-Id": "user-1",
    "X-Customer-Id": "CUST-001",
    "X-Roles": "customer",
}


def test_metrics_expose_route_and_operation_contract_without_customer_labels(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'observability.db'}",
        support_message_rate_limit=0,
        app_env="development",
        auth_mode="dev",
        mcp_auth_mode="disabled",
        seed_demo_data=True,
    )
    container = build_container(settings)
    with TestClient(create_app(container)) as client:
        knowledge = client.post(
            "/v1/support/messages",
            headers=CUSTOMER_HEADERS,
            json={"conversation_id": "CONV-OBS-1", "message": "退款政策是什么？"},
        )
        order = client.post(
            "/v1/support/messages",
            headers=CUSTOMER_HEADERS,
            json={"conversation_id": "CONV-OBS-2", "message": "订单状态 ORD-1001"},
        )
        assert knowledge.status_code == 200
        assert order.status_code == 200

        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        body = metrics.text
        assert "supportops_http_requests_total" in body
        assert 'route="/v1/support/messages"' in body
        assert 'operation="agent.route"' in body
        assert 'operation="retrieval.hybrid.search"' in body
        assert 'operation="tool.order.get"' in body
        assert "CUST-001" not in body
        assert "CONV-OBS-1" not in body


def test_metrics_can_be_disabled(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'metrics-disabled.db'}",
        metrics_enabled=False,
        app_env="development",
        auth_mode="dev",
        mcp_auth_mode="disabled",
        seed_demo_data=True,
    )
    container = build_container(settings)
    with TestClient(create_app(container)) as client:
        response = client.get("/metrics")
        assert response.status_code == 404


def test_typed_dependency_error_increments_degradation_metric():
    observability = Observability(
        Settings(
            app_env="development", auth_mode="dev", mcp_auth_mode="disabled", seed_demo_data=True
        )
    )

    class TypedFailure(RuntimeError):
        reason = "redis_unavailable"

    try:
        with observability.operation(
            "reliability.test",
            component="reliability",
            backend="redis",
        ):
            raise TypedFailure("boom")
    except TypedFailure:
        pass
    else:
        raise AssertionError("typed failure should propagate")

    body = observability.render_metrics().decode()
    assert 'component="reliability",reason="redis_unavailable"' in body
    assert (
        'backend="redis",component="reliability",operation="reliability.test",outcome="error"'
        in body
    )
    observability.shutdown()


def test_trace_context_header_is_accepted_without_entering_metric_labels(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'traceparent.db'}",
        app_env="development",
        auth_mode="dev",
        mcp_auth_mode="disabled",
        seed_demo_data=True,
    )
    container = build_container(settings)
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    with TestClient(create_app(container)) as client:
        response = client.get("/health", headers={"traceparent": traceparent})
        assert response.status_code == 200
        body = client.get("/metrics").text
        assert "4bf92f3577b34da6a3ce929d0e0e4736" not in body
