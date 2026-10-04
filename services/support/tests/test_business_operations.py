from datetime import UTC, datetime, timedelta
from time import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.auth import AuthorizationError, Principal
from app.config import Settings
from app.container import build_container
from app.domain import OperationStatus, PendingActionKind
from app.main import create_app
from app.operations import CustomerContext, OperationConflict
from app.store import (
    ActionReceiptRow,
    ApprovalDecisionRow,
    AuditEventRow,
    OrderRow,
    PendingActionRow,
)

TENANT = "TENANT-DEMO"


@pytest.fixture
def services(tmp_path):
    c = build_container(
        Settings(
            app_env="development",
            auth_mode="dev",
            mcp_auth_mode="disabled",
            seed_demo_data=True,
            database_url=f"sqlite:///{tmp_path / 'operations.db'}",
        )
    )
    yield c
    c.observability.shutdown()
    c.store.engine.dispose()


def context(customer="CUST-001", tenant=TENANT):
    return CustomerContext(customer, "customer-1", "TRC-OP", tenant)


def admin(tenant=TENANT, subject="admin-1"):
    return Principal(
        subject=subject,
        tenant_id=tenant,
        customer_id=None,
        roles=frozenset({"support_admin"}),
        authentication_method="jwt",
    )


def prepare(c, *, high=False, kind=PendingActionKind.REFUND, conversation="CONV"):
    return c.operations.prepare(
        kind=kind,
        order_id="ORD-2001" if high else "ORD-1001",
        conversation_id=conversation,
        context=context("CUST-002" if high else "CUST-001"),
    ).operation


def resolve(c, operation, confirm=True, *, high=False):
    return c.operations.resolve(
        operation.operation_id, confirm=confirm, context=context("CUST-002" if high else "CUST-001")
    )


def decide(c, operation, decision="approve", actor=None):
    return c.operations.decide(
        operation.operation_id,
        decision=decision,
        expected_version=operation.version,
        principal=actor or admin(),
        trace_id="TRC-DECISION",
    )


def mutate(c, **values):
    with c.store._session.begin() as session:
        session.execute(update(OrderRow).where(OrderRow.order_id == "ORD-1001").values(**values))


def no_submission(c, operation):
    with c.store._session() as session:
        assert session.get(ActionReceiptRow, operation.operation_id) is None
        assert session.get(OrderRow, operation.order_id).refund_status == "none"


@pytest.mark.parametrize("kind", [PendingActionKind.REFUND, PendingActionKind.RETURN_REQUEST])
def test_sequential_preparation_reuses_operation_and_submitted_result(services, kind):
    first = prepare(services, kind=kind)
    second = prepare(services, kind=kind, conversation="OTHER-CONVERSATION")
    assert first.operation_id == second.operation_id
    assert first.status is OperationStatus.PENDING_CONFIRMATION
    submitted = resolve(services, first)
    assert submitted.status is OperationStatus.SUBMITTED
    assert submitted.result == resolve(services, first).result
    again = prepare(services, kind=kind)
    assert again.operation_id == first.operation_id
    assert again.result == submitted.result
    with services.store._session() as session:
        assert len(session.scalars(select(PendingActionRow)).all()) == 1
        assert len(session.scalars(select(ActionReceiptRow)).all()) == 1


@pytest.mark.parametrize("customer_first", [True, False])
def test_high_risk_requires_two_independent_durable_authorizations(services, customer_first):
    operation = prepare(services, high=True)
    assert operation.status is OperationStatus.PENDING_APPROVAL
    assert operation.ticket_id
    if customer_first:
        middle = resolve(services, operation, high=True)
        assert middle.customer_confirmed and not middle.human_approved
        no_submission(services, middle)
        submitted = decide(services, middle)
    else:
        middle = decide(services, operation)
        assert middle.human_approved and not middle.customer_confirmed
        assert middle.status is OperationStatus.PENDING_CONFIRMATION
        no_submission(services, middle)
        submitted = resolve(services, middle, high=True)
    assert submitted.status is OperationStatus.SUBMITTED
    assert submitted.result["amount"] == 899
    with services.store._session() as session:
        row = session.get(PendingActionRow, operation.operation_id)
        approval = session.get(ApprovalDecisionRow, operation.operation_id)
        assert row.confirmed_by == "customer-1" and row.confirmed_at is not None
        assert row.confirmed_snapshot_hash == row.snapshot_hash == approval.snapshot_hash
        assert approval.actor_id == "admin-1" and approval.decided_at is not None
        assert approval.snapshot["amount"] == 899
        assert approval.operation_version >= 1 and approval.snapshot_version == row.snapshot_version
        assert approval.snapshot == row.snapshot
        assert session.get(ActionReceiptRow, operation.operation_id).operation_id == row.action_id


@pytest.mark.parametrize(
    "values,reason",
    [
        ({"refundable_amount": 899}, "refund_proposal_changed"),
        ({"refundable_amount": 99}, "refund_proposal_changed"),
        ({"refundable_amount": 0}, "order_not_refundable"),
        ({"customer_id": "CUST-OTHER"}, "order_not_found"),
        ({"refund_status": "requested"}, "order_request_already_recorded"),
    ],
)
def test_authorization_drift_is_durable_and_old_operation_never_revives(services, values, reason):
    operation = prepare(services)
    mutate(services, **values)
    invalidated = resolve(services, operation)
    assert invalidated.status is OperationStatus.INVALIDATED
    assert invalidated.result["reason"] == reason
    mutate(services, refundable_amount=199, customer_id="CUST-001", refund_status="none")
    assert resolve(services, operation).status is OperationStatus.INVALIDATED
    no_submission(services, operation)
    replacement = prepare(services)
    assert replacement.operation_id != operation.operation_id
    assert not replacement.customer_confirmed and not replacement.human_approved


@pytest.mark.parametrize("terminal", ["cancel", "expire", "reject", "invalidate"])
def test_terminal_replay_and_new_attempt_require_fresh_authority(services, terminal):
    high = terminal == "reject"
    operation = prepare(services, high=high)
    if terminal == "cancel":
        ended = resolve(services, operation, False)
        expected = OperationStatus.CANCELLED
    elif terminal == "expire":
        with services.store._session.begin() as session:
            session.get(PendingActionRow, operation.operation_id).expires_at = datetime.now(
                UTC
            ) - timedelta(seconds=1)
        ended = resolve(services, operation)
        expected = OperationStatus.EXPIRED
    elif terminal == "reject":
        ended = decide(services, operation, "reject")
        expected = OperationStatus.REJECTED
    else:
        mutate(services, refundable_amount=99)
        ended = resolve(services, operation)
        expected = OperationStatus.INVALIDATED
        mutate(services, refundable_amount=199)
    assert ended.status is expected
    assert resolve(services, operation, high=high).result == ended.result
    replacement = prepare(services, high=high)
    assert replacement.operation_id != operation.operation_id
    assert not replacement.customer_confirmed and not replacement.human_approved
    assert resolve(services, operation, high=high).status is expected
    no_submission(services, replacement)


@pytest.mark.parametrize("drift", ["amount", "eligibility", "policy", "approval_snapshot"])
def test_approval_never_authorizes_changed_snapshot(services, drift, monkeypatch):
    operation = prepare(services, high=True)
    approved = decide(services, operation)
    if drift in {"amount", "eligibility"}:
        with services.store._session.begin() as session:
            session.get(OrderRow, "ORD-2001").refundable_amount = 999 if drift == "amount" else 0
    elif drift == "policy":
        monkeypatch.setattr(services.operations.policy, "refund_human_review_threshold", 1000)
    else:
        with services.store._session.begin() as session:
            record = session.get(ApprovalDecisionRow, operation.operation_id)
            record.snapshot = {**record.snapshot, "amount": 199}
    outcome = resolve(services, approved, high=True)
    assert outcome.status is OperationStatus.INVALIDATED
    no_submission(services, operation)


@pytest.mark.parametrize(
    "actor",
    [
        Principal(
            subject="customer",
            tenant_id=TENANT,
            customer_id="CUST-002",
            roles=frozenset({"customer"}),
            scopes=frozenset({"support:write"}),
            authentication_method="jwt",
        ),
        Principal(
            subject="agent",
            tenant_id=TENANT,
            customer_id=None,
            roles=frozenset({"support_agent"}),
            scopes=frozenset({"support:read", "support:write"}),
            authentication_method="jwt",
        ),
        Principal(
            subject="dev-admin",
            tenant_id=TENANT,
            customer_id=None,
            roles=frozenset({"support_admin"}),
        ),
    ],
)
def test_approval_requires_verified_jwt_admin(services, actor):
    operation = prepare(services, high=True)
    with pytest.raises(AuthorizationError, match="jwt_support_admin_required"):
        decide(services, operation, actor=actor)
    no_submission(services, operation)


def test_first_approval_decision_wins_and_opposite_decision_conflicts(services):
    operation = prepare(services, high=True)
    approved = decide(services, operation, actor=admin(subject="first"))
    replay = decide(services, operation, actor=admin(subject="second"))
    assert replay.version == approved.version
    with pytest.raises(OperationConflict, match="approval_decision_conflict"):
        decide(services, operation, "reject")
    with services.store._session() as session:
        assert session.get(ApprovalDecisionRow, operation.operation_id).actor_id == "first"


@pytest.mark.parametrize(
    "stage",
    [
        "before_order_update",
        "after_order_update",
        "after_receipt",
        "after_audit",
        "after_terminal_update",
    ],
)
def test_atomic_submission_rolls_back_at_every_failure_checkpoint(services, stage, monkeypatch):
    operation = prepare(services)

    def fail(current):
        if current == stage:
            raise RuntimeError("injected_failure")

    monkeypatch.setattr(services.operations, "_checkpoint", fail)
    with pytest.raises(RuntimeError, match="injected_failure"):
        resolve(services, operation)
    no_submission(services, operation)
    with services.store._session() as session:
        row = session.get(PendingActionRow, operation.operation_id)
        assert (
            row.status == "pending_confirmation" and row.confirmed_at is None and row.version == 1
        )
        assert not session.scalars(
            select(AuditEventRow).where(
                AuditEventRow.operation_id == row.action_id,
                AuditEventRow.operation.in_(
                    ["refund.execute", "operation.submitted", "operation.customer_confirmed"]
                ),
            )
        ).all()
    monkeypatch.setattr(services.operations, "_checkpoint", lambda _: None)
    assert resolve(services, operation).status is OperationStatus.SUBMITTED
    assert resolve(services, operation).result["amount"] == 199


def test_business_audit_failure_rolls_back_order_receipt_and_confirmation(services, monkeypatch):
    operation = prepare(services)
    audit = services.operations._audit

    def broken(*args, **kwargs):
        if kwargs["event"] == "refund.execute":
            raise RuntimeError("audit_unavailable")
        return audit(*args, **kwargs)

    monkeypatch.setattr(services.operations, "_audit", broken)
    with pytest.raises(RuntimeError, match="audit_unavailable"):
        resolve(services, operation)
    no_submission(services, operation)
    with services.store._session() as session:
        assert session.get(PendingActionRow, operation.operation_id).confirmed_at is None


@pytest.mark.parametrize("body", [{}, {"confirm": None}, {"confirm": "true"}, {"confirm": 1}])
def test_empty_or_nonboolean_confirmation_cannot_authorize(services, body):
    operation = prepare(services)
    with TestClient(create_app(services)) as client:
        response = client.post(
            f"/v1/actions/{operation.operation_id}/confirm",
            json=body,
            headers={
                "X-Principal-Id": "customer-1",
                "X-Customer-Id": "CUST-001",
                "X-Roles": "customer",
            },
        )
    assert response.status_code == 422
    no_submission(services, operation)


def test_tenant_and_customer_boundaries_cover_status_confirmation_and_decisions(services):
    operation = prepare(services, high=True)
    for foreign in [context("CUST-002", "TENANT-B"), context("CUST-001")]:
        with pytest.raises(ValueError, match="pending_action_not_found"):
            services.operations.resolve(operation.operation_id, confirm=True, context=foreign)
        with pytest.raises(ValueError, match="pending_action_not_found"):
            services.operations.resolve(operation.operation_id, confirm=False, context=foreign)
    with pytest.raises(ValueError, match="pending_action_not_found"):
        services.operations.get(operation.operation_id, principal=admin("TENANT-B"))
    with pytest.raises(ValueError, match="pending_action_not_found"):
        decide(services, operation, actor=admin("TENANT-B"))


def test_http_high_risk_flow_and_customer_safe_visibility(services):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # Use a separately built JWT app sharing the test database, not dev headers.
    settings = Settings(
        database_url=services.settings.database_url,
        auth_issuer="https://issuer.example",
        auth_audience="supportops",
        auth_jwks_url="https://issuer.example/jwks",
    )
    c = build_container(settings)
    c.auth._jwks_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
    )

    def headers(customer=None, roles=None, tenant=TENANT):
        now = int(time())
        token = jwt.encode(
            dict(
                sub="admin" if customer is None else "customer",
                tenant_id=tenant,
                customer_id=customer,
                roles=roles or ["support_admin"],
                iss=settings.auth_issuer,
                aud=settings.auth_audience,
                exp=now + 300,
                iat=now,
            ),
            key,
            algorithm="RS256",
        )
        return {"Authorization": "Bearer " + token}

    try:
        with TestClient(create_app(c)) as client:
            prepared = client.post(
                "/v1/support/messages",
                headers=headers("CUST-002", ["customer"]),
                json={"conversation_id": "HIGH", "message": "refund ORD-2001"},
            ).json()
            op = prepared["operation_id"]
            assert prepared["handoff"] and prepared["pending_action_id"] is None
            linked = client.get(
                f"/v1/agent/tickets/{prepared['ticket_id']}/operation", headers=headers()
            ).json()
            assert linked["operation_id"] == op
            confirmed = client.post(
                f"/v1/actions/{op}/confirm",
                headers=headers("CUST-002", ["customer"]),
                json={"confirm": True},
            ).json()
            assert confirmed["operation_status"] == "pending_approval"
            state = client.get(
                f"/v1/operations/{op}", headers=headers("CUST-002", ["customer"])
            ).json()
            assert "actor_id" not in str(state) and "snapshot_hash" not in str(state)
            denied = client.post(
                f"/v1/agent/operations/{op}/decision",
                headers=headers(None, ["support_agent"]),
                json={"decision": "approve", "expected_version": state["version"]},
            )
            assert denied.status_code == 403
            submitted = client.post(
                f"/v1/agent/operations/{op}/decision",
                headers=headers(),
                json={"decision": "approve", "expected_version": state["version"]},
            )
            assert submitted.status_code == 200 and submitted.json()["status"] == "submitted"
            assert submitted.json()["result"]["refund_status"] == "requested"
    finally:
        c.store.engine.dispose()
        c.observability.shutdown()


def test_stale_approval_revision_requires_refresh(services):
    operation = prepare(services, high=True)
    confirmed = resolve(services, operation, high=True)
    with pytest.raises(OperationConflict, match="operation_version_conflict"):
        decide(services, operation)
    with services.store._session() as session:
        assert session.get(ApprovalDecisionRow, operation.operation_id) is None
    assert decide(services, confirmed).status is OperationStatus.SUBMITTED


def test_different_tenant_and_customer_business_scopes_remain_independent(services):
    with services.store._session.begin() as session:
        session.add(
            OrderRow(
                tenant_id="TENANT-B",
                order_id="ORD-9001",
                customer_id="CUST-001",
                status="delivered",
                amount=199,
                refundable_amount=199,
                refund_status="none",
                return_status="none",
            )
        )
    a = prepare(services)
    b = services.operations.prepare(
        kind=PendingActionKind.REFUND,
        order_id="ORD-9001",
        conversation_id="CONV",
        context=context(tenant="TENANT-B"),
    ).operation
    assert a.operation_id != b.operation_id
    assert (
        services.operations.resolve(
            b.operation_id, confirm=True, context=context(tenant="TENANT-B")
        ).status
        is OperationStatus.SUBMITTED
    )
    no_submission(services, a)
    wrong = services.operations.prepare(
        kind=PendingActionKind.REFUND,
        order_id="ORD-1001",
        conversation_id="CONV",
        context=context(customer="CUST-002"),
    )
    assert wrong.operation is None and wrong.reason == "order_not_found"


@pytest.mark.parametrize("customer", [None, "", " "])
def test_customer_context_cannot_fall_back_to_employee_scope(customer):
    with pytest.raises(ValueError, match="invalid_customer_context"):
        CustomerContext(customer, "actor", "trace", TENANT)
