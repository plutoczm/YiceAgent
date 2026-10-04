import asyncio
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import Principal
from app.config import Settings
from app.container import build_container
from app.domain import PendingActionStatus, SupportRequest, TicketPriority
from app.main import create_app
from app.policy import ActionPolicy
from app.store import ActionReceiptRow, OrderRow, PendingActionRow
from app.workflows import (
    ConfirmActionRequest,
    CustomerContext,
    OrderLookupRequest,
    OrderLookupResult,
    RefundEligibilityResult,
    RefundPreparationRequest,
    RefundPreparationResult,
    ReturnPreparationRequest,
    ReturnPreparationResult,
)


@pytest.fixture
def services(tmp_path):
    container = build_container(
        Settings(
            database_url=f"sqlite:///{tmp_path / 's1.db'}",
            app_env="development",
            auth_mode="dev",
            mcp_auth_mode="disabled",
            seed_demo_data=True,
        )
    )
    yield container
    container.observability.shutdown()
    container.store.engine.dispose()


def principal(customer_id="CUST-001"):
    return Principal(
        subject=f"user:{customer_id}",
        customer_id=customer_id,
        roles=frozenset({"customer"}),
        tenant_id="TENANT-DEMO",
    )


def prepare(services, order_id="ORD-1001", customer_id="CUST-001"):
    return services.orchestrator.prepare_refund(
        customer_id=customer_id,
        actor_id="user:test",
        conversation_id="S1",
        order_id=order_id,
        tenant_id="TENANT-DEMO",
    )


def change_order(services, **values):
    with services.store._session.begin() as session:
        order = session.get(OrderRow, "ORD-1001")
        for name, value in values.items():
            setattr(order, name, value)


def assert_no_refund_write(services, action_id, expected_status=PendingActionStatus.PENDING):
    with services.store._session() as session:
        assert session.get(OrderRow, "ORD-1001").refund_status == "none"
        assert session.get(ActionReceiptRow, action_id) is None
    assert (
        services.store.get_pending_action(action_id, tenant_id="TENANT-DEMO").status
        is expected_status
    )


@pytest.mark.parametrize("amount", [99.0, 200.0, 899.0])
def test_refund_amount_drift_requires_new_proposal(services, amount):
    prepared = prepare(services)
    action_id = prepared.pending_action_id
    change_order(services, refundable_amount=amount)
    resolved = services.orchestrator.resolve_action(action_id, principal(), confirm=True)
    assert resolved.status.value == "invalidated"
    assert resolved.result["reason"] == "refund_proposal_changed"
    assert_no_refund_write(services, action_id, expected_status=PendingActionStatus.INVALIDATED)


@pytest.mark.parametrize("amount", [0.0, -1.0])
def test_refund_eligibility_disappears_before_confirmation(services, amount):
    prepared = prepare(services)
    change_order(services, refundable_amount=amount)
    resolved = services.orchestrator.resolve_action(
        prepared.pending_action_id, principal(), confirm=True
    )
    assert resolved.status.value == "invalidated"
    assert resolved.result["reason"] == "order_not_refundable"
    assert_no_refund_write(
        services, prepared.pending_action_id, expected_status=PendingActionStatus.INVALIDATED
    )


@pytest.mark.parametrize("amount,handoff", [(0.01, False), (500.0, False), (500.01, True)])
def test_refund_threshold_and_positive_amount(services, amount, handoff):
    change_order(services, refundable_amount=amount)
    prepared = prepare(services)
    assert prepared.handoff is handoff
    if handoff:
        assert prepared.pending_action_id is None
        assert (
            services.store.get_ticket(prepared.ticket_id, tenant_id="TENANT-DEMO").priority
            is TicketPriority.HIGH
        )
    else:
        pending = services.store.get_pending_action(
            prepared.pending_action_id, tenant_id="TENANT-DEMO"
        )
        assert pending.payload["amount"] == amount
        assert pending.status is PendingActionStatus.PENDING
    with services.store._session() as session:
        assert session.scalars(select(ActionReceiptRow)).all() == []


@pytest.mark.parametrize("order_id", ["ORD-2001", "ORD-9999"])
def test_unowned_and_missing_refund_have_same_failure(services, order_id):
    result = prepare(services, order_id)
    assert result.pending_action_id is None
    assert result.handoff is False
    assert "order_not_found" in result.answer


@pytest.mark.parametrize("amount", [0.0, -1.0])
def test_nonpositive_refund_cannot_be_prepared(services, amount):
    change_order(services, refundable_amount=amount)
    result = prepare(services)
    assert result.pending_action_id is None
    assert "order_not_refundable" in result.answer


def test_ownership_change_before_confirmation_is_rejected(services):
    prepared = prepare(services)
    change_order(services, customer_id="CUST-002")
    resolved = services.orchestrator.resolve_action(
        prepared.pending_action_id, principal(), confirm=True
    )
    assert resolved.status.value == "invalidated"
    assert resolved.result["reason"] == "order_not_found"
    assert_no_refund_write(
        services, prepared.pending_action_id, expected_status=PendingActionStatus.INVALIDATED
    )


@pytest.mark.parametrize("order_id", ["ORD-1002", "ORD-2001", "ORD-9999"])
def test_unavailable_return_uses_existing_ticket_workflow(services, order_id):
    result = services.orchestrator.handle(
        SupportRequest(conversation_id="S1-RETURN", message=f"\u9000\u8d27 {order_id}"), principal()
    )
    assert result.handoff is True
    assert result.pending_action_id is None
    ticket = services.store.get_ticket(result.ticket_id, tenant_id="TENANT-DEMO")
    assert ticket.priority is TicketPriority.NORMAL
    assert ticket.customer_id == "CUST-001"
    assert ticket.reason == f"return_review:{order_id}"


def context(customer_id="CUST-001"):
    return CustomerContext(
        customer_id=customer_id, actor_id="user:test", trace_id="TRC-S1", tenant_id="TENANT-DEMO"
    )


@pytest.mark.parametrize(
    "order_id,found,reason",
    [
        ("ORD-1001", True, "eligible"),
        ("ORD-1002", True, "order_not_refundable"),
        ("ORD-2001", False, "order_not_found"),
        ("ORD-9999", False, "order_not_found"),
    ],
)
def test_typed_order_lookup_and_eligibility(services, order_id, found, reason):
    workflows = services.tools.workflows
    lookup = workflows.lookup_order(OrderLookupRequest(order_id), context=context())
    eligibility = workflows.refund_eligibility(OrderLookupRequest(order_id), context=context())
    assert isinstance(lookup, OrderLookupResult)
    assert (lookup.order is not None) is found
    assert isinstance(eligibility, RefundEligibilityResult)
    assert eligibility.reason_code == reason
    assert eligibility.eligible is (reason == "eligible")
    assert eligibility.requires_customer_confirmation is (reason == "eligible")
    assert eligibility.requires_human_review is False


def test_typed_high_value_eligibility(services):
    result = services.tools.workflows.refund_eligibility(
        OrderLookupRequest("ORD-2001"), context=context("CUST-002")
    )
    assert result.eligible
    assert result.quoted_amount == 899
    assert result.requires_human_review
    assert not result.requires_customer_confirmation


@pytest.mark.parametrize("confirm", [False, None, "true", 1])
def test_tools_cannot_submit_without_explicit_confirmation(services, confirm):
    prepared = prepare(services)
    with pytest.raises(ValueError, match="customer_confirmation_required"):
        services.tools.submit_confirmed_action(
            request=ConfirmActionRequest(prepared.pending_action_id, confirm=confirm),
            context=context(),
        )
    assert_no_refund_write(services, prepared.pending_action_id)


def test_tools_require_persisted_action_and_own_customer(services):
    with pytest.raises(ValueError, match="pending_action_not_found"):
        services.tools.submit_confirmed_action(
            request=ConfirmActionRequest("ACT-UNKNOWN", True), context=context()
        )
    prepared = prepare(services)
    with pytest.raises(ValueError, match="pending_action_not_found"):
        services.tools.submit_confirmed_action(
            request=ConfirmActionRequest(prepared.pending_action_id, True),
            context=context("CUST-002"),
        )
    assert_no_refund_write(services, prepared.pending_action_id)
    for surface in [services.tools, services.tools.workflows, services.store]:
        assert not hasattr(surface, "execute_refund")
        assert not hasattr(surface, "execute_return")


@pytest.mark.parametrize("amount", [None, "199", True, -1, float("inf")])
def test_invalid_prepared_amount_cannot_authorize_submission(services, amount):
    prepared = prepare(services)
    with services.store._session.begin() as session:
        pending = session.get(PendingActionRow, prepared.pending_action_id)
        pending.payload = {"order_id": "ORD-1001", "amount": amount}
    resolved = services.orchestrator.resolve_action(
        prepared.pending_action_id, principal(), confirm=True
    )
    assert resolved.status.value == "invalidated"
    assert resolved.result["reason"] == "refund_proposal_changed"
    assert_no_refund_write(
        services, prepared.pending_action_id, expected_status=PendingActionStatus.INVALIDATED
    )


def test_current_policy_is_rechecked_without_amount_change(services, monkeypatch):
    prepared = prepare(services)
    monkeypatch.setattr(services.tools.workflows.policy, "refund", ActionPolicy(100).refund)
    resolved = services.orchestrator.resolve_action(
        prepared.pending_action_id, principal(), confirm=True
    )
    assert resolved.status.value == "invalidated"
    assert resolved.result["reason"] == "authorization_snapshot_changed"
    assert_no_refund_write(
        services, prepared.pending_action_id, expected_status=PendingActionStatus.INVALIDATED
    )


def test_authoritative_locked_read_does_not_expand_authority(services, monkeypatch):
    prepared = prepare(services)
    locked = services.operations._locked

    def drift_before_lock(*args, **kwargs):
        change_order(services, refundable_amount=899)
        return locked(*args, **kwargs)

    monkeypatch.setattr(services.operations, "_locked", drift_before_lock)
    result = services.orchestrator.resolve_action(
        prepared.pending_action_id, principal(), confirm=True
    )
    assert result.status is PendingActionStatus.INVALIDATED
    assert result.result["reason"] == "refund_proposal_changed"
    assert_no_refund_write(services, prepared.pending_action_id, PendingActionStatus.INVALIDATED)


def test_invalidated_drift_audit_and_terminal_cancellation_replay(services):
    prepared = prepare(services)
    change_order(services, refundable_amount=899)
    invalidated = services.tools.submit_confirmed_action(
        request=ConfirmActionRequest(prepared.pending_action_id, True), context=context()
    )
    assert invalidated.status.value == "invalidated"
    event = next(
        e
        for e in services.store.get_trace_audit("TRC-S1", tenant_id="TENANT-DEMO")
        if e.operation == "operation.invalidated"
    )
    assert event.outcome == "invalidated"
    assert set(event.details) == {"reason", "from", "to", "version"}
    replay = services.orchestrator.resolve_action(
        prepared.pending_action_id, principal(), confirm=False
    )
    assert replay.status is PendingActionStatus.INVALIDATED
    assert replay.result == invalidated.result


@pytest.mark.parametrize("terminal", ["cancelled", "expired", "executed"])
def test_direct_submission_replays_terminal_action(services, terminal):
    prepared = prepare(services)
    action_id = prepared.pending_action_id
    if terminal == "expired":
        with services.store._session.begin() as session:
            session.get(PendingActionRow, action_id).expires_at = datetime.now(UTC) - timedelta(
                seconds=1
            )
    previous = services.orchestrator.resolve_action(
        action_id, principal(), confirm=terminal != "cancelled"
    )
    replay = services.tools.submit_confirmed_action(
        request=ConfirmActionRequest(action_id, True), context=context()
    )
    assert replay.status.value == {"executed": "submitted"}.get(terminal, terminal)
    assert replay.result == previous.result
    with services.store._session() as session:
        assert (session.get(ActionReceiptRow, action_id) is not None) is (terminal == "executed")
        assert session.get(OrderRow, "ORD-1001").refund_status == (
            "requested" if terminal == "executed" else "none"
        )


def test_direct_submission_durably_expires_pending_action(services):
    prepared = prepare(services)
    with services.store._session.begin() as session:
        session.get(PendingActionRow, prepared.pending_action_id).expires_at = datetime.now(
            UTC
        ) - timedelta(seconds=1)
    result = services.tools.submit_confirmed_action(
        request=ConfirmActionRequest(prepared.pending_action_id, True), context=context()
    )
    assert result.status.value == "expired"
    assert_no_refund_write(services, prepared.pending_action_id, PendingActionStatus.EXPIRED)


def test_return_state_drift_rejected_before_receipt(services):
    prepared = services.tools.prepare_return(
        request=ReturnPreparationRequest("ORD-1001", "S1"), context=context()
    )
    assert isinstance(prepared, ReturnPreparationResult)
    assert prepared.pending.payload == {"order_id": "ORD-1001"}
    change_order(services, status="shipped")
    resolved = services.orchestrator.resolve_action(
        prepared.pending.action_id, principal(), confirm=True
    )
    assert resolved.status.value == "invalidated"
    assert resolved.result["reason"] == "return_not_available"
    with services.store._session() as session:
        assert session.get(OrderRow, "ORD-1001").return_status == "none"
        assert session.get(ActionReceiptRow, prepared.pending.action_id) is None


def test_tools_and_orchestrator_delegate_preparation(services, monkeypatch):
    workflows = services.tools.workflows
    refund = RefundPreparationResult(RefundEligibilityResult(False, "order_not_refundable"))
    seen = []

    def decide(request, *, context):
        seen.append((request, context))
        return refund

    monkeypatch.setattr(workflows, "prepare_refund", decide)
    result = prepare(services)
    assert "order_not_refundable" in result.answer
    assert seen == [(RefundPreparationRequest("ORD-1001", "S1"), seen[0][1])]
    assert seen[0][1].customer_id == "CUST-001"
    assert seen[0][1].actor_id == "user:test"
    assert (
        services.store.get_order("ORD-1001", "CUST-001", tenant_id="TENANT-DEMO").refundable_amount
        == 199
    )

    lookup = OrderLookupResult(None)
    monkeypatch.setattr(workflows, "lookup_order", lambda request, *, context: lookup)
    assert (
        services.tools.get_order(
            order_id="ORD-1001",
            customer_id="CUST-001",
            actor_id="user:test",
            trace_id="TRC-S1",
            tenant_id="TENANT-DEMO",
        )
        is None
    )


def test_workflow_runs_with_model_and_retrieval_imports_blocked(tmp_path):
    script = """
import importlib.abc
import sys

class BlockExternalAI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname in {'app.knowledge', 'app.retrieval', 'app.knowledge_ingestion',
                        'app.model_gateway', 'app.router', 'app.orchestrator',
                        'fastapi', 'langgraph'}:
            raise AssertionError('unexpected dependency: ' + fullname)

sys.meta_path.insert(0, BlockExternalAI())
from app.policy import ActionPolicy
from app.reliability import LocalReliabilityCoordinator
from app.store import SupportStore
from app.tickets import TicketWorkflow
from app.workflows import (SupportWorkflows, CustomerContext, OrderLookupRequest,
    RefundPreparationRequest, ReturnPreparationRequest, ConfirmActionRequest)

store = SupportStore('sqlite:///:memory:')
store.create_schema()
store.seed_demo_data(tenant_id="TENANT-DEMO")
flows = SupportWorkflows(store=store, tickets=TicketWorkflow(store), policy=ActionPolicy(500),
    reliability=LocalReliabilityCoordinator(), action_confirmation_ttl_seconds=600)
ctx = CustomerContext('CUST-001', 'test', 'trace', tenant_id='TENANT-DEMO')
assert flows.lookup_order(OrderLookupRequest('ORD-1001'), context=ctx).order.order_id == 'ORD-1001'
refund = flows.prepare_refund(RefundPreparationRequest('ORD-1001', 'test'), context=ctx)
assert flows.submit_confirmed_action(
    ConfirmActionRequest(refund.pending.action_id, True), context=ctx).result["amount"] == 199
returned = flows.prepare_return(ReturnPreparationRequest('ORD-1001', 'test'), context=ctx)
assert flows.submit_confirmed_action(
    ConfirmActionRequest(returned.pending.action_id, True),
    context=ctx).result['return_status'] == 'requested'
store.engine.dispose()
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_http_refund_drift_returns_durable_invalidated_state(services):
    prepared = prepare(services)
    change_order(services, refundable_amount=899)
    with TestClient(create_app(services)) as client:
        response = client.post(
            f"/v1/actions/{prepared.pending_action_id}/confirm",
            headers={
                "X-Principal-Id": "user:test",
                "X-Customer-Id": "CUST-001",
                "X-Roles": "customer",
            },
            json={"confirm": True},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "invalidated"
    assert response.json()["result"] == {"reason": "refund_proposal_changed"}
    assert_no_refund_write(services, prepared.pending_action_id, PendingActionStatus.INVALIDATED)


def test_mcp_refund_uses_same_preparation_and_exposes_no_raw_execution(services):
    from mcp import Client

    from app.mcp_server import build_mcp_server

    async def run():
        async with Client(build_mcp_server(services)) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "search_support_knowledge",
                "get_order",
                "request_refund",
                "get_ticket",
            }
            own = await client.call_tool(
                "request_refund", {"order_id": "ORD-1001", "conversation_id": "S1-MCP"}
            )
            other = await client.call_tool(
                "request_refund", {"order_id": "ORD-2001", "conversation_id": "S1-MCP"}
            )
            return own.structured_content, other.structured_content

    own, other = asyncio.run(run())
    pending = services.store.get_pending_action(own["pending_action_id"], tenant_id="TENANT-DEMO")
    assert pending.customer_id == "CUST-001"
    assert pending.payload == {"order_id": "ORD-1001", "amount": 199.0}
    assert other["pending_action_id"] is None
    assert "order_not_found" in other["answer"]
    assert_no_refund_write(services, pending.action_id)


def test_submission_audit_failure_rolls_back_and_retry_replays(services, monkeypatch):
    prepared = prepare(services)
    action_id = prepared.pending_action_id
    audit = services.operations._audit

    def unavailable(*args, **kwargs):
        if kwargs["event"] == "refund.execute":
            raise RuntimeError("audit_unavailable")
        return audit(*args, **kwargs)

    monkeypatch.setattr(services.operations, "_audit", unavailable)
    with pytest.raises(RuntimeError, match="audit_unavailable"):
        services.orchestrator.resolve_action(action_id, principal(), confirm=True)
    with services.store._session() as session:
        assert session.get(OrderRow, "ORD-1001").refund_status == "none"
        assert session.get(ActionReceiptRow, action_id) is None
        row = session.get(PendingActionRow, action_id)
        assert row.status == "pending_confirmation" and row.confirmed_at is None
    monkeypatch.setattr(services.operations, "_audit", audit)
    result = services.orchestrator.resolve_action(action_id, principal(), confirm=True)
    replay = services.orchestrator.resolve_action(action_id, principal(), confirm=True)
    assert result.status is PendingActionStatus.EXECUTED
    assert (
        result.result
        == replay.result
        == {"order_id": "ORD-1001", "refund_status": "requested", "amount": 199.0}
    )
    with services.store._session() as session:
        assert len(session.scalars(select(ActionReceiptRow)).all()) == 1
