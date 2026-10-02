"""Demo-only identities reuse production domain guards without changing JWT."""

import secrets

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthorizationError, Principal
from app.demo.composition import build_demo_container, create_demo_app
from app.demo.identity import PERSONAS
from app.store import SupportStore


@pytest.fixture
def demo(tmp_path):
    store = SupportStore("sqlite:///" + str(tmp_path / "test.db"))
    store.create_schema()
    tokens = {name: secrets.token_urlsafe(32) for name in PERSONAS}
    container = build_demo_container(store, tokens)
    with TestClient(create_demo_app(container)) as client:
        yield client, container, tokens
    store.engine.dispose()


def test_opaque_tokens_and_role_headers(demo):
    client, container, tokens = demo
    for header in ({}, {"Authorization": "Bearer wrong"}, {"X-Roles": "support_admin"}):
        assert client.get("/v1/agent/tickets", headers=header).status_code == 401
    headers = {
        "Authorization": "Bearer " + tokens["customer-a1"],
        "X-Roles": "support_admin",
        "X-Customer-Id": "customer-a2",
        "X-Tenant": "tenant-b",
    }
    assert client.get("/v1/agent/tickets", headers=headers).status_code == 403
    principal = container.auth.authenticate(authorization=headers["Authorization"])
    assert principal.customer_id == "customer-a1" and principal.tenant_id == "tenant-a"
    with pytest.raises(AuthorizationError):
        principal.require_approver()
    operator = container.auth.authenticate(authorization="Bearer " + tokens["operator-a"])
    operator.require_approver()
    assert operator.authentication_method == "demo-opaque"
    # The original Principal still rejects demo identities as production approvers.
    with pytest.raises(AuthorizationError):
        Principal(
            "operator",
            "tenant-a",
            None,
            frozenset({"support_admin"}),
            authentication_method="demo-opaque",
        ).require_approver()


def test_offline_constructs_no_model_or_embedding(demo, monkeypatch):
    from app import retrieval

    def fail(*args, **kwargs):
        raise AssertionError("embedding constructed")

    monkeypatch.setattr(retrieval.DeterministicHashEmbedding, "__init__", fail)
    _, old, tokens = demo
    container = build_demo_container(old.store, tokens)
    assert container.knowledge.model is None and container.orchestrator.router.model is None
    evidence = container.knowledge.search("refund policy")
    assert evidence and evidence[0].source_uri and evidence[0].chunk_id
    assert container.knowledge.search("zzzzqxyunrelated") == []
    assert "[" in container.knowledge.answer("refund", evidence)


def test_explicit_action_rejects_scope_injection(demo):
    client, _, tokens = demo
    headers = {"Authorization": "Bearer " + tokens["customer-a1"]}
    assert (
        client.post(
            "/internal/demo/actions",
            headers=headers,
            json={
                "action": "refund",
                "order_id": "ORD-1001",
                "conversation_id": "x",
                "tenant_id": "tenant-b",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/internal/demo/actions",
            headers=headers,
            json={
                "action": "refund",
                "order_id": "ORD-1001",
                "conversation_id": "x",
            },
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/internal/demo/actions",
            headers={"Authorization": "Bearer " + tokens["operator-a"]},
            json={"action": "handoff", "conversation_id": "x"},
        ).status_code
        == 403
    )


def test_wrong_target_token_rejected(demo):
    client, _, tokens = demo
    assert (
        client.get(
            "/v1/agent/tickets",
            headers={
                "Authorization": "Bearer " + secrets.token_urlsafe(32),
            },
        ).status_code
        == 401
    )
