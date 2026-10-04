from app.auth import Principal
from app.config import Settings
from app.container import build_container
from app.domain import PendingActionStatus, SupportRequest, TicketPriority


def build_test_container(tmp_path):
    return build_container(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'supportops.db'}",
            app_env="development",
            auth_mode="dev",
            mcp_auth_mode="disabled",
            seed_demo_data=True,
        )
    )


def customer(customer_id: str = "CUST-001") -> Principal:
    return Principal(
        subject=f"user:{customer_id}",
        customer_id=customer_id,
        roles=frozenset({"customer"}),
        tenant_id="TENANT-DEMO",
    )


def test_refund_requires_confirmation_and_is_idempotent(tmp_path):
    container = build_test_container(tmp_path)
    response = container.orchestrator.handle(
        SupportRequest(conversation_id="CONV-1", message="我要退款 ORD-1001"),
        customer(),
    )
    assert response.pending_action_id is not None
    before = container.store.get_order("ORD-1001", "CUST-001", tenant_id="TENANT-DEMO")
    assert before is not None
    assert before.refund_status == "none"

    first = container.orchestrator.resolve_action(
        response.pending_action_id,
        customer(),
        confirm=True,
    )
    second = container.orchestrator.resolve_action(
        response.pending_action_id,
        customer(),
        confirm=True,
    )
    assert first.status is PendingActionStatus.EXECUTED
    assert first.result == second.result
    assert first.result["refund_status"] == "requested"


def test_customer_can_cancel_pending_action_without_side_effect(tmp_path):
    container = build_test_container(tmp_path)
    response = container.orchestrator.handle(
        SupportRequest(conversation_id="CONV-CANCEL", message="退款 ORD-1001"),
        customer(),
    )
    result = container.orchestrator.resolve_action(
        response.pending_action_id or "",
        customer(),
        confirm=False,
    )
    assert result.status is PendingActionStatus.CANCELLED
    order = container.store.get_order("ORD-1001", "CUST-001", tenant_id="TENANT-DEMO")
    assert order is not None
    assert order.refund_status == "none"


def test_high_value_refund_creates_high_priority_handoff(tmp_path):
    container = build_test_container(tmp_path)
    response = container.orchestrator.handle(
        SupportRequest(conversation_id="CONV-2", message="退款 ORD-2001"),
        customer("CUST-002"),
    )
    assert response.handoff is True
    ticket = container.store.get_ticket(response.ticket_id or "", tenant_id="TENANT-DEMO")
    assert ticket is not None
    assert ticket.priority is TicketPriority.HIGH
    assert ticket.sla_breached is False


def test_cross_customer_order_is_not_disclosed(tmp_path):
    container = build_test_container(tmp_path)
    response = container.orchestrator.handle(
        SupportRequest(conversation_id="CONV-3", message="订单状态 ORD-2001"),
        customer(),
    )
    assert "未找到" in response.answer
    assert "delivered" not in response.answer


def test_return_requires_confirmation(tmp_path):
    container = build_test_container(tmp_path)
    response = container.orchestrator.handle(
        SupportRequest(conversation_id="CONV-4", message="退货 ORD-1001"),
        customer(),
    )
    assert response.pending_action_id is not None
    result = container.orchestrator.resolve_action(
        response.pending_action_id,
        customer(),
        confirm=True,
    )
    assert result.result["return_status"] == "requested"


def test_trace_contains_route_policy_prepare_and_execute_events(tmp_path):
    container = build_test_container(tmp_path)
    response = container.orchestrator.handle(
        SupportRequest(conversation_id="CONV-AUDIT", message="退款 ORD-1001"),
        customer(),
    )
    initial_events = container.store.get_trace_audit(response.trace_id, tenant_id="TENANT-DEMO")
    operations = {event.operation for event in initial_events}
    assert {"agent.route", "refund.quote", "policy.refund", "action.prepare"} <= operations

    result = container.orchestrator.resolve_action(
        response.pending_action_id or "",
        customer(),
        confirm=True,
    )
    execution_events = container.store.get_trace_audit(result.trace_id, tenant_id="TENANT-DEMO")
    assert "refund.execute" in {event.operation for event in execution_events}
