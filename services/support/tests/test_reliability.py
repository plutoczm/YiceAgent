import time

from fastapi.testclient import TestClient

from app.auth import Principal
from app.config import Settings
from app.container import build_container
from app.domain import PendingActionStatus, SupportRequest
from app.main import create_app
from app.reliability import (
    LocalReliabilityCoordinator,
    ReliabilityBackendError,
)

CUSTOMER_HEADERS = {
    "X-Principal-Id": "user-1",
    "X-Customer-Id": "CUST-001",
    "X-Roles": "customer",
}


def customer() -> Principal:
    return Principal(
        subject="user-1",
        customer_id="CUST-001",
        roles=frozenset({"customer"}),
        tenant_id="TENANT-DEMO",
    )


def test_local_rate_limit_is_atomic_per_window():
    coordinator = LocalReliabilityCoordinator()
    first = coordinator.check_rate_limit(
        scope="support",
        subject="user-1",
        limit=2,
        window_seconds=60,
    )
    second = coordinator.check_rate_limit(
        scope="support",
        subject="user-1",
        limit=2,
        window_seconds=60,
    )
    third = coordinator.check_rate_limit(
        scope="support",
        subject="user-1",
        limit=2,
        window_seconds=60,
    )
    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.remaining == 0


def test_api_rate_limit_returns_429_with_retry_after(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'rate.db'}",
        support_message_rate_limit=1,
        app_env="development",
        auth_mode="dev",
        mcp_auth_mode="disabled",
        seed_demo_data=True,
    )
    client = TestClient(create_app(build_container(settings)))
    payload = {"conversation_id": "CONV-RATE", "message": "订单状态 ORD-1001"}
    first = client.post("/v1/support/messages", headers=CUSTOMER_HEADERS, json=payload)
    assert first.status_code == 200
    limited = client.post("/v1/support/messages", headers=CUSTOMER_HEADERS, json=payload)
    assert limited.status_code == 429
    assert limited.json()["detail"] == "rate_limit_exceeded"
    assert int(limited.headers["Retry-After"]) >= 1


def test_confirmation_expires_without_business_side_effect(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'expiry.db'}",
        action_confirmation_ttl_seconds=1,
        app_env="development",
        auth_mode="dev",
        mcp_auth_mode="disabled",
        seed_demo_data=True,
    )
    container = build_container(settings)
    prepared = container.orchestrator.handle(
        SupportRequest(conversation_id="CONV-EXP", message="退款 ORD-1001"),
        customer(),
    )
    assert prepared.pending_action_id is not None
    assert prepared.pending_action_expires_at is not None
    time.sleep(1.05)
    resolved = container.orchestrator.resolve_action(
        prepared.pending_action_id,
        customer(),
        confirm=True,
    )
    assert resolved.status is PendingActionStatus.EXPIRED
    order = container.store.get_order("ORD-1001", "CUST-001", tenant_id="TENANT-DEMO")
    assert order is not None
    assert order.refund_status == "none"


def test_redis_action_lock_is_not_the_business_correctness_boundary(tmp_path):
    container = build_container(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'lock.db'}",
            app_env="development",
            auth_mode="dev",
            mcp_auth_mode="disabled",
            seed_demo_data=True,
        )
    )
    prepared = container.orchestrator.handle(
        SupportRequest(conversation_id="CONV-LOCK", message="退款 ORD-1001"),
        customer(),
    )
    action_id = prepared.pending_action_id or ""
    token = container.reliability.acquire_action_lock(action_id, 10)
    assert token is not None
    try:
        first = container.orchestrator.resolve_action(action_id, customer(), confirm=True)
        replay = container.orchestrator.resolve_action(action_id, customer(), confirm=True)
        assert first.result == replay.result
        assert first.status is PendingActionStatus.EXECUTED
    finally:
        container.reliability.release_action_lock(action_id, token)
    order = container.store.get_order("ORD-1001", "CUST-001", tenant_id="TENANT-DEMO")
    assert order is not None
    assert order.refund_status == "requested"


def test_database_resolution_remains_replay_safe_when_redis_is_unavailable(tmp_path):
    container = build_container(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'closed.db'}",
            app_env="development",
            auth_mode="dev",
            mcp_auth_mode="disabled",
            seed_demo_data=True,
        )
    )
    prepared = container.orchestrator.handle(
        SupportRequest(conversation_id="CONV-CLOSED", message="退款 ORD-1001"),
        customer(),
    )

    class BrokenReliability:
        backend_name = "broken"

        def acquire_action_lock(self, action_id: str, ttl_seconds: int):
            del action_id, ttl_seconds
            raise ReliabilityBackendError(
                "redis_unavailable",
                operation="redis.action_lock.acquire",
            )

        def release_action_lock(self, action_id: str, token: str):
            del action_id, token

        def register_action_ttl(self, action_id: str, ttl_seconds: int):
            del action_id, ttl_seconds

        def clear_action_ttl(self, action_id: str):
            del action_id

    container.reliability._delegate = BrokenReliability()
    first = container.orchestrator.resolve_action(
        prepared.pending_action_id, customer(), confirm=True
    )
    replay = container.orchestrator.resolve_action(
        prepared.pending_action_id, customer(), confirm=True
    )
    assert first.result == replay.result
    assert first.status is PendingActionStatus.EXECUTED
    order = container.store.get_order("ORD-1001", "CUST-001", tenant_id="TENANT-DEMO")
    assert order is not None
    assert order.refund_status == "requested"
