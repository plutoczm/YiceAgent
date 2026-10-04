from __future__ import annotations

from app.domain import OperationView, OrderView, TicketPriority, TicketView
from app.workflows import (
    ConfirmActionRequest,
    CustomerContext,
    OrderLookupRequest,
    RefundPreparationRequest,
    RefundPreparationResult,
    ReturnPreparationRequest,
    ReturnPreparationResult,
    SupportWorkflows,
)


class SupportTools:
    """Thin adapters. Customer identity is supplied only by trusted entry-point code."""

    def __init__(self, workflows: SupportWorkflows) -> None:
        self.workflows = workflows

    def get_order(
        self,
        *,
        tenant_id: str,
        order_id: str,
        customer_id: str,
        trace_id: str,
        actor_id: str,
    ) -> OrderView | None:
        return self.workflows.lookup_order(
            OrderLookupRequest(order_id),
            context=CustomerContext(customer_id, actor_id, trace_id, tenant_id=tenant_id),
        ).order

    def prepare_refund(
        self,
        *,
        request: RefundPreparationRequest,
        context: CustomerContext,
    ) -> RefundPreparationResult:
        return self.workflows.prepare_refund(request, context=context)

    def prepare_return(
        self,
        *,
        request: ReturnPreparationRequest,
        context: CustomerContext,
    ) -> ReturnPreparationResult:
        return self.workflows.prepare_return(request, context=context)

    def submit_confirmed_action(
        self,
        *,
        request: ConfirmActionRequest,
        context: CustomerContext,
    ) -> OperationView:
        return self.workflows.submit_confirmed_action(request, context=context)

    def resolve_action(
        self,
        *,
        request: ConfirmActionRequest,
        context: CustomerContext,
    ) -> OperationView:
        return self.workflows.resolve_action(request, context=context)

    def create_ticket(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        conversation_id: str,
        reason: str,
        trace_id: str,
        actor_id: str,
        priority: TicketPriority = TicketPriority.NORMAL,
    ) -> TicketView:
        return self.workflows.create_ticket(
            context=CustomerContext(customer_id, actor_id, trace_id, tenant_id=tenant_id),
            conversation_id=conversation_id,
            reason=reason,
            priority=priority,
        )
