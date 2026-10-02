import asyncio
from dataclasses import FrozenInstanceError
from time import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.sql import visitors
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.selectable import Select

from app.auth import AuthenticationError, AuthService, Principal
from app.config import Settings
from app.container import build_container
from app.domain import (
    Intent,
    PendingActionKind,
    PendingActionStatus,
    RoutingDecision,
    TicketMessageRole,
    TicketStatus,
)
from app.main import create_app
from app.mcp_server import JwtTokenVerifier, build_mcp_server
from app.operations import CustomerContext, OperationConflict
from app.store import (
    ActionReceiptRow,
    AuditEventRow,
    OrderRow,
    PendingActionRow,
    SupportStore,
    TicketEventRow,
    TicketMessageRow,
    TicketRow,
)
from app.tenancy import TenantId

A = "TENANT-A"
B = "TENANT-B"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwt_settings(**kwargs):
    return Settings(
        auth_issuer="https://identity.example",
        auth_audience="supportops",
        auth_jwks_url="https://identity.example/jwks",
        **kwargs,
    )


def token_for(
    key, tenant=A, customer="CUST-001", roles=None, scope="support:read support:write", **changes
):
    now = int(time())
    claims = dict(
        sub="user-1",
        tenant_id=tenant,
        customer_id=customer,
        roles=roles if roles is not None else ["customer"],
        scope=scope,
        iss="https://identity.example",
        aud="supportops",
        iat=now,
        exp=now + 300,
    )
    claims.update(changes)
    if claims.get("tenant_id") == "__missing__":
        del claims["tenant_id"]
    return jwt.encode(claims, key, algorithm="RS256")


def authenticate(auth, token, **headers):
    return auth.authenticate(
        authorization="Bearer " + token,
        dev_principal_id=headers.get("subject"),
        dev_customer_id=headers.get("customer"),
        dev_roles=headers.get("roles"),
    )


@pytest.fixture
def services(tmp_path, signing_key):
    container = build_container(jwt_settings(database_url=f"sqlite:///{tmp_path / 'tenants.db'}"))
    container.auth._jwks_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=signing_key.public_key())
    )
    with container.store._session.begin() as session:
        for order_id, tenant, customer in [
            ("ORD-1001", A, "CUST-001"),
            ("ORD-1002", A, "CUST-002"),
            ("ORD-2001", B, "CUST-001"),
        ]:
            session.add(
                OrderRow(
                    order_id=order_id,
                    tenant_id=tenant,
                    customer_id=customer,
                    status="delivered",
                    amount=199,
                    refundable_amount=199,
                    refund_status="none",
                    return_status="none",
                )
            )
    yield container
    container.observability.shutdown()
    container.store.engine.dispose()


def headers(key, tenant=A, customer="CUST-001", roles=None, **changes):
    return {"Authorization": "Bearer " + token_for(key, tenant, customer, roles, **changes)}


def prepare(services, tenant=A, order="ORD-1001", conversation="CONV"):
    return services.orchestrator.prepare_refund(
        tenant_id=tenant,
        customer_id="CUST-001",
        actor_id="user-1",
        conversation_id=conversation,
        order_id=order,
    )


def principal(tenant=A):
    return Principal(
        subject="user-1",
        tenant_id=TenantId(tenant),
        customer_id="CUST-001",
        roles=frozenset({"customer"}),
    )


@pytest.mark.parametrize(
    "tenant", ["__missing__", "", " ", None, 7, [], {}, "x" * 65, "a/b", "a\nb"]
)
def test_jwt_missing_or_invalid_tenant_is_rejected(services, signing_key, tenant):
    expected = "token_tenant_required" if tenant == "__missing__" else "invalid_token_tenant"
    with pytest.raises(AuthenticationError, match=expected):
        authenticate(services.auth, token_for(signing_key, tenant=tenant))


@pytest.mark.parametrize(
    "changes",
    [
        {"sub": ""},
        {"sub": None},
        {"sub": 5},
        {"customer_id": 7},
        {"customer_id": None},
        {"roles": {"support_admin": True}},
        {"scope": ["support:write"]},
        {"iss": "https://wrong.example"},
        {"aud": "wrong"},
        {"exp": 1},
        {"iat": int(time()) + 600},
    ],
)
def test_jwt_required_identity_and_verified_claims(services, signing_key, changes):
    with pytest.raises(AuthenticationError):
        authenticate(services.auth, token_for(signing_key, **changes))


def test_signature_and_forged_development_headers_do_not_change_jwt_identity(services, signing_key):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthenticationError, match="invalid_access_token"):
        authenticate(services.auth, token_for(other_key))
    identity = authenticate(
        services.auth,
        token_for(signing_key),
        subject="forged-agent",
        customer="CUST-002",
        roles="support_admin",
    )
    assert (identity.tenant_id, identity.subject, identity.customer_id) == (A, "user-1", "CUST-001")
    assert identity.roles == frozenset({"customer"})
    with pytest.raises(FrozenInstanceError):
        identity.tenant_id = B
    agent = authenticate(
        services.auth, token_for(signing_key, customer=None, roles=["support_agent"], sub="agent-1")
    )
    agent.require_agent()
    assert agent.customer_id is None


@pytest.mark.parametrize(
    "environment,auth,mcp,valid",
    [
        ("development", "dev", "disabled", True),
        ("development", "jwt", "jwt", True),
        ("production", "jwt", "jwt", True),
        ("production", "dev", "jwt", False),
        ("production", "jwt", "disabled", False),
        ("production", "dev", "disabled", False),
    ],
)
def test_effective_security_configurations(environment, auth, mcp, valid):
    settings = jwt_settings(app_env=environment, auth_mode=auth, mcp_auth_mode=mcp)
    if valid:
        settings.validate_security()
    else:
        with pytest.raises(ValueError, match="production_requires_jwt_auth"):
            settings.validate_security()


def test_unconfigured_default_fails_before_database_creation(monkeypatch):
    for key in [
        "APP_ENV",
        "AUTH_MODE",
        "AUTH_ISSUER",
        "AUTH_AUDIENCE",
        "AUTH_JWKS_URL",
        "MCP_AUTH_MODE",
        "SEED_DEMO_DATA",
    ]:
        monkeypatch.delenv(key, raising=False)
    settings = Settings.from_env()
    assert (settings.app_env, settings.auth_mode, settings.mcp_auth_mode) == (
        "production",
        "jwt",
        "jwt",
    )
    monkeypatch.setattr(SupportStore, "__init__", lambda *args, **kwargs: pytest.fail("DB opened"))
    with pytest.raises(ValueError, match="jwt_auth_not_configured"):
        build_container(settings)
    with pytest.raises(ValueError, match="production_requires_jwt_auth"):
        build_container(Settings(auth_mode="dev"))


def test_development_identity_is_explicit_and_server_tenant_is_authoritative():
    auth = AuthService(
        Settings(app_env="development", auth_mode="dev", mcp_auth_mode="disabled", dev_tenant_id=A)
    )
    identity = auth.authenticate(
        authorization=None,
        dev_principal_id="local",
        dev_customer_id="CUST-001",
        dev_roles="customer",
    )
    assert identity.tenant_id == A
    with pytest.raises(AuthenticationError, match="conflicting_identity_sources"):
        auth.authenticate(
            authorization="Bearer anything",
            dev_principal_id="local",
            dev_customer_id="CUST-001",
            dev_roles="customer",
        )


def test_production_headers_alone_cannot_authenticate(services):
    with TestClient(create_app(services)) as client:
        response = client.get(
            "/v1/orders/ORD-1001",
            headers={
                "X-Tenant-Id": A,
                "X-Principal-Id": "user-1",
                "X-Customer-Id": "CUST-001",
                "X-Roles": "support_admin",
            },
        )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", B),
        ("customer_id", "CUST-002"),
        ("roles", ["support_admin"]),
        ("actor_id", "agent-1"),
        ("subject", "agent-1"),
        ("scopes", ["support:write"]),
    ],
)
def test_http_body_cannot_override_identity(services, signing_key, field, value):
    with TestClient(create_app(services)) as client:
        response = client.post(
            "/v1/support/messages",
            headers=headers(signing_key),
            json={
                "conversation_id": "CONV",
                "message": "refund ORD-1001",
                field: value,
            },
        )
    assert response.status_code == 422
    with services.store._session() as session:
        assert session.scalars(select(PendingActionRow)).all() == []


def test_order_sql_customer_and_tenant_scope_with_guessed_ids(services, signing_key):
    with TestClient(create_app(services)) as client:
        own = client.get("/v1/orders/ORD-1001", headers=headers(signing_key))
        same_tenant_other_customer = client.get("/v1/orders/ORD-1002", headers=headers(signing_key))
        foreign = client.get("/v1/orders/ORD-2001", headers=headers(signing_key))
        missing = client.get("/v1/orders/ORD-9999", headers=headers(signing_key))
        own_b = client.get("/v1/orders/ORD-2001", headers=headers(signing_key, tenant=B))
        forged = headers(signing_key)
        forged.update({"X-Tenant-Id": B, "X-Customer-Id": "CUST-002", "X-Roles": "support_admin"})
        still_a = client.get("/v1/orders/ORD-2001?tenant_id=TENANT-B", headers=forged)
    assert own.status_code == own_b.status_code == 200
    assert own.json()["customer_id"] == own_b.json()["customer_id"] == "CUST-001"
    assert "tenant_id" not in own.json()
    assert (
        same_tenant_other_customer.status_code == foreign.status_code == missing.status_code == 404
    )
    assert foreign.json() == missing.json() == still_a.json()


@pytest.mark.parametrize("confirm", [True, False])
def test_foreign_tenant_cannot_confirm_or_cancel_action(services, signing_key, confirm):
    prepared = prepare(services, tenant=B, order="ORD-2001")
    with TestClient(create_app(services)) as client:
        response = client.post(
            f"/v1/actions/{prepared.pending_action_id}/confirm",
            headers=headers(signing_key),
            json={"confirm": confirm},
        )
    assert response.status_code == 404
    pending = services.store.get_pending_action(prepared.pending_action_id, tenant_id=B)
    assert pending.status is PendingActionStatus.PENDING
    assert services.store.get_pending_action(prepared.pending_action_id, tenant_id=A) is None
    with services.store._session() as session:
        assert session.get(OrderRow, "ORD-2001").refund_status == "none"
        assert session.scalars(select(ActionReceiptRow)).all() == []


def test_foreign_tenant_cannot_replay_or_reuse_receipt(services, signing_key):
    prepared = prepare(services, tenant=B, order="ORD-2001")
    own = services.orchestrator.resolve_action(
        prepared.pending_action_id, principal(B), confirm=True
    )
    assert own.result["amount"] == 199
    with TestClient(create_app(services)) as client:
        denied = client.post(
            f"/v1/actions/{prepared.pending_action_id}/confirm",
            headers=headers(signing_key),
            json={"confirm": True},
        )
        status = client.get(
            f"/v1/operations/{prepared.pending_action_id}", headers=headers(signing_key)
        )
    assert denied.status_code == status.status_code == 404
    assert not hasattr(services.store, "_record_refund_request")
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), services.store._session.begin() as session:
        session.add(
            ActionReceiptRow(
                tenant_id=A,
                operation_id=prepared.pending_action_id,
                idempotency_key=prepared.pending_action_id,
                action_kind="refund",
                legacy=False,
                result={"order_id": "ORD-1001", "amount": 199},
            )
        )
    assert services.store.get_order("ORD-1001", "CUST-001", tenant_id=A).refund_status == "none"


@pytest.mark.parametrize("wrong_kind,wrong_order", [(True, False), (False, True)])
def test_same_tenant_receipt_must_match_operation_and_order(services, wrong_kind, wrong_order):
    from sqlalchemy.exc import IntegrityError

    prepared = prepare(services)
    receipt = ActionReceiptRow(
        tenant_id=A,
        operation_id=prepared.pending_action_id,
        idempotency_key=prepared.pending_action_id,
        legacy=False,
        action_kind="return_request" if wrong_kind else "refund",
        result={
            "order_id": "ORD-2001" if wrong_order else "ORD-1001",
            "refund_status": "requested",
            "amount": 199,
        },
    )
    if wrong_kind:
        with pytest.raises(IntegrityError), services.store._session.begin() as session:
            session.add(receipt)
    else:
        with services.store._session.begin() as session:
            session.add(receipt)
        with pytest.raises(OperationConflict, match="unexpected_receipt_for_active_operation"):
            services.orchestrator.resolve_action(
                prepared.pending_action_id, principal(), confirm=True
            )
    assert services.store.get_order("ORD-1001", "CUST-001", tenant_id=A).refund_status == "none"


def test_foreign_refund_and_return_preparation_cannot_access_order(services, signing_key):
    foreign = prepare(services, order="ORD-2001")
    assert foreign.pending_action_id is None
    assert "order_not_found" in foreign.answer
    with TestClient(create_app(services)) as client:
        returned = client.post(
            "/v1/support/messages",
            headers=headers(signing_key),
            json={
                "conversation_id": "RETURN",
                "message": "\u9000\u8d27 ORD-2001",
            },
        )
    assert returned.status_code == 200
    assert returned.json()["handoff"] is True
    ticket_id = returned.json()["ticket_id"]
    assert services.store.get_ticket(ticket_id, tenant_id=A) is not None
    assert services.store.get_ticket(ticket_id, tenant_id=B) is None
    assert services.store.get_order("ORD-2001", "CUST-001", tenant_id=B).return_status == "none"


def test_tickets_messages_recovery_and_agents_are_tenant_scoped(services, signing_key):
    a = services.tickets.create_handoff(
        tenant_id=A, customer_id="CUST-001", conversation_id="SAME-CONV", reason="A"
    )
    b = services.tickets.create_handoff(
        tenant_id=B, customer_id="CUST-001", conversation_id="SAME-CONV", reason="B"
    )
    services.store.create_ticket_message(
        tenant_id=B,
        ticket_id=b.ticket_id,
        sender_id="user-1",
        sender_role=TicketMessageRole.CUSTOMER,
        body="B private message",
    )
    agent_a = headers(signing_key, customer=None, roles=["support_agent"], sub="agent-1")
    with TestClient(create_app(services)) as client:
        recovered = client.get("/v1/conversations/SAME-CONV/ticket", headers=headers(signing_key))
        assert recovered.json()["ticket_id"] == a.ticket_id
        assert [
            t["ticket_id"] for t in client.get("/v1/agent/tickets", headers=agent_a).json()
        ] == [a.ticket_id]
        for path in [f"/v1/tickets/{b.ticket_id}", f"/v1/tickets/{b.ticket_id}/messages"]:
            assert client.get(path, headers=headers(signing_key)).status_code == 404
        assert (
            client.post(
                f"/v1/tickets/{b.ticket_id}/messages",
                headers=headers(signing_key),
                json={"message": "foreign write"},
            ).status_code
            == 404
        )
        for suffix, body in [
            ("assign", {"assignee_id": "agent-1"}),
            ("transition", {"status": "assigned"}),
            ("messages", {"message": "foreign agent write"}),
        ]:
            assert (
                client.post(
                    f"/v1/agent/tickets/{b.ticket_id}/{suffix}", headers=agent_a, json=body
                ).status_code
                == 404
            )
        assert (
            client.get(f"/v1/agent/tickets/{b.ticket_id}/messages", headers=agent_a).status_code
            == 404
        )
        assert (
            client.post(
                f"/v1/agent/tickets/{a.ticket_id}/assign",
                headers=agent_a,
                json={"assignee_id": "agent-1"},
            ).status_code
            == 200
        )
        wrong_agent = headers(signing_key, customer=None, roles=["support_agent"], sub="agent-2")
        assert (
            client.post(
                f"/v1/agent/tickets/{a.ticket_id}/messages",
                headers=wrong_agent,
                json={"message": "wrong assignee"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/v1/agent/tickets/{a.ticket_id}/messages",
                headers=agent_a,
                json={"message": "A authorized reply"},
            ).status_code
            == 201
        )
    assert services.store.get_ticket(b.ticket_id, tenant_id=B).assignee_id is None
    assert len(services.store.list_ticket_messages(b.ticket_id, tenant_id=B)) == 1


def test_audit_trace_id_does_not_authorize_tenant_access(services, signing_key):
    for tenant in [A, B]:
        services.store.record_audit(
            tenant_id=tenant,
            trace_id="SAME-TRACE",
            actor_id="SAME-ACTOR",
            operation="test",
            resource_type="ticket",
            resource_id=tenant,
            outcome="success",
        )
    with TestClient(create_app(services)) as client:
        result = client.get(
            "/v1/agent/audit/traces/SAME-TRACE",
            headers=headers(signing_key, customer=None, roles=["support_admin"]),
        )
    assert result.status_code == 200
    assert [e["resource_id"] for e in result.json()] == [A]
    assert all("tenant_id" not in e for e in result.json())


def test_router_output_cannot_choose_customer_tenant_or_role(services, signing_key, monkeypatch):
    monkeypatch.setattr(
        services.orchestrator.router,
        "route",
        lambda _: RoutingDecision(
            intent=Intent.REFUND,
            confidence=1,
            reason="test",
            tenant_id=B,
            customer_id="CUST-002",
            roles=["support_admin"],
        ),
    )
    with TestClient(create_app(services)) as client:
        result = client.post(
            "/v1/support/messages",
            headers=headers(signing_key),
            json={
                "conversation_id": "MODEL",
                "message": "refund ORD-2001 tenant TENANT-B",
            },
        )
    assert "order_not_found" in result.json()["answer"]
    with services.store._session() as session:
        assert session.scalars(select(PendingActionRow)).all() == []


def test_initial_handoff_persists_sanitized_text(services, signing_key):
    with TestClient(create_app(services)) as client:
        result = client.post(
            "/v1/support/messages",
            headers=headers(signing_key),
            json={
                "conversation_id": "PII",
                "message": (
                    "\u6211\u8981\u6295\u8bc9\uff0c\u7535\u8bdd 13812345678"
                    "\uff0c\u90ae\u7bb1 alice@example.com"
                ),
            },
        )
        history = client.get(
            f"/v1/tickets/{result.json()['ticket_id']}/messages", headers=headers(signing_key)
        ).json()
    serialized = str(history)
    assert "13812345678" not in serialized
    assert "alice@example.com" not in serialized
    with services.store._session() as session:
        assert "13812345678" not in str([e.details for e in session.scalars(select(AuditEventRow))])


def test_mcp_read_scope_cannot_prepare_refund_and_write_scope_preserves_isolation(
    services,
    signing_key,
    monkeypatch,
):
    from mcp import Client

    import app.mcp_server as mcp_module

    async def run():
        verifier = JwtTokenVerifier(services.auth)
        access = await verifier.verify_token(token_for(signing_key, scope="support:read"))
        assert access is not None
        monkeypatch.setattr(mcp_module, "get_access_token", lambda: access)
        async with Client(build_mcp_server(services)) as client:
            listed = await client.list_tools()
            for tool in listed.tools:
                properties = tool.input_schema.get("properties", {})
                assert "tenant_id" not in properties and "customer_id" not in properties
            own = await client.call_tool("get_order", {"order_id": "ORD-1001"})
            assert own.structured_content["found"]
            denied = await client.call_tool(
                "request_refund", {"order_id": "ORD-1001", "conversation_id": "MCP"}
            )
            assert denied.is_error
            assert any("insufficient_scope" in block.text for block in denied.content)
        with services.store._session() as session:
            assert session.scalars(select(PendingActionRow)).all() == []
            assert session.scalars(select(TicketRow)).all() == []
        access = await verifier.verify_token(token_for(signing_key))
        async with Client(build_mcp_server(services)) as client:
            foreign = await client.call_tool(
                "request_refund",
                {
                    "order_id": "ORD-2001",
                    "conversation_id": "MCP",
                },
            )
            assert foreign.structured_content["pending_action_id"] is None
            normal = await client.call_tool(
                "request_refund",
                {
                    "order_id": "ORD-1001",
                    "conversation_id": "MCP",
                },
            )
            pending = services.store.get_pending_action(
                normal.structured_content["pending_action_id"], tenant_id=A, customer_id="CUST-001"
            )
            assert pending.tenant_id == A
        assert await verifier.verify_token(token_for(signing_key, tenant="__missing__")) is None

    asyncio.run(run())


def test_effective_select_and_update_queries_have_tenant_predicates(services):
    seen = []

    def observe(connection, clause, multiparams, params, execution_options):
        if isinstance(clause, (Select, Update)):
            seen.append(clause)

    event.listen(services.store.engine, "before_execute", observe)
    try:
        prepared = prepare(services)
        services.orchestrator.resolve_action(prepared.pending_action_id, principal(), confirm=True)
        ticket = services.tickets.create_handoff(
            tenant_id=A, customer_id="CUST-001", conversation_id="SQL", reason="synthetic"
        )
        services.tickets.assign(
            tenant_id=A,
            ticket_id=ticket.ticket_id,
            assignee_id="agent-1",
            actor_id="agent-1",
            trace_id="SQL",
        )
        services.store.create_ticket_message(
            tenant_id=A,
            customer_id="CUST-001",
            ticket_id=ticket.ticket_id,
            sender_role=TicketMessageRole.CUSTOMER,
            sender_id="user-1",
            body="synthetic",
        )
        services.store.list_ticket_messages(ticket.ticket_id, tenant_id=A, customer_id="CUST-001")
        services.store.list_tickets(tenant_id=A)
        services.store.find_latest_ticket_for_conversation(
            tenant_id=A, customer_id="CUST-001", conversation_id="SQL"
        )
        services.tickets.transition(
            tenant_id=A,
            ticket_id=ticket.ticket_id,
            target=TicketStatus.PENDING_CUSTOMER,
            actor_id="agent-1",
            trace_id="SQL",
            note=None,
        )
        services.store.get_trace_audit("SQL", tenant_id=A)
        second = services.operations.prepare(
            kind=PendingActionKind.RETURN_REQUEST,
            order_id="ORD-1001",
            conversation_id="SQL",
            context=CustomerContext("CUST-001", "user-1", "SQL", A),
        ).operation
        services.orchestrator.resolve_action(second.operation_id, principal(), confirm=False)
        services.operations.expire_due(tenant_id=A)
    finally:
        event.remove(services.store.engine, "before_execute", observe)
    assert len(seen) >= 20
    assert any(isinstance(statement, Update) for statement in seen)
    for statement in seen:
        conditions = [
            node for criterion in statement._where_criteria for node in visitors.iterate(criterion)
        ]
        assert any(
            getattr(getattr(node, "left", None), "name", None) == "tenant_id"
            and getattr(getattr(node, "right", None), "value", None) == A
            for node in conditions
        ), str(statement)


def test_all_owned_rows_require_tenant_and_no_store_api_has_implicit_tenant(services):
    import inspect

    models = [
        OrderRow,
        TicketRow,
        TicketEventRow,
        TicketMessageRow,
        PendingActionRow,
        ActionReceiptRow,
        AuditEventRow,
    ]
    for model in models:
        column = model.__table__.c.tenant_id
        assert not column.nullable
        assert column.default is None and column.server_default is None
    for name in [
        "get_order",
        "get_ticket",
        "list_tickets",
        "get_pending_action",
        "create_ticket",
        "create_ticket_message",
        "list_ticket_messages",
        "get_trace_audit",
        "record_audit",
    ]:
        parameter = inspect.signature(getattr(services.store, name)).parameters["tenant_id"]
        assert parameter.default is inspect.Parameter.empty


def test_production_demo_seed_and_symmetric_jwt_config_are_rejected():
    with pytest.raises(ValueError, match="production_demo_seed_forbidden"):
        jwt_settings(seed_demo_data=True).validate_security()
    with pytest.raises(ValueError, match="unsupported_jwt_algorithm"):
        jwt_settings(auth_algorithm="HS256").validate_security()


@pytest.mark.parametrize("value", ["", "a/b", "x" * 65, None])
def test_store_rejects_invalid_tenant_scope(services, value):
    with pytest.raises(ValueError, match="invalid_tenant_id"):
        services.store.get_order("ORD-1001", "CUST-001", tenant_id=value)
