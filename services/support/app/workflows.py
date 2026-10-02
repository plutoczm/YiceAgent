from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from typing import Literal

from app.domain import (
    OperationView,
    OrderView,
    PendingActionKind,
    PendingActionView,
    PolicyAction,
    PolicyDecision,
    TicketPriority,
    TicketView,
)
from app.operations import BusinessOperations, CustomerContext
from app.policy import ActionPolicy
from app.reliability import (
    LocalReliabilityCoordinator,
    RedisReliabilityCoordinator,
)
from app.store import SupportStore
from app.tickets import TicketWorkflow


@dataclass(frozen=True)
class OrderLookupRequest:
    order_id: str


@dataclass(frozen=True)
class OrderLookupResult:
    order: OrderView | None


@dataclass(frozen=True)
class RefundEligibilityResult:
    eligible: bool
    reason_code: Literal[
        "eligible", "order_not_found", "order_not_refundable", "order_request_already_recorded"
    ]
    quoted_amount: float | None = None
    decision: PolicyDecision | None = None

    @property
    def requires_customer_confirmation(self) -> bool:
        return self.decision is not None and (
            self.decision.action is PolicyAction.REQUIRE_CONFIRMATION
        )

    @property
    def requires_human_review(self) -> bool:
        return self.decision is not None and self.decision.action is PolicyAction.REQUIRE_HUMAN


@dataclass(frozen=True)
class RefundPreparationRequest:
    order_id: str
    conversation_id: str


@dataclass(frozen=True)
class RefundPreparationResult:
    eligibility: RefundEligibilityResult
    pending: PendingActionView | None = None
    ticket: TicketView | None = None
    operation: OperationView | None = None


@dataclass(frozen=True)
class ReturnPreparationRequest:
    order_id: str
    conversation_id: str


@dataclass(frozen=True)
class ReturnPreparationResult:
    pending: PendingActionView | None = None
    ticket: TicketView | None = None
    operation: OperationView | None = None


@dataclass(frozen=True)
class ConfirmActionRequest:
    action_id: str
    confirm: bool


class SupportWorkflows:
    """Read adapters and preparation results around the durable operation workflow."""

    def __init__(
        self,
        *,
        store: SupportStore,
        tickets: TicketWorkflow,
        policy: ActionPolicy,
        reliability: LocalReliabilityCoordinator | RedisReliabilityCoordinator,
        action_confirmation_ttl_seconds: int,
    ) -> None:
        self.store = store
        self.tickets = tickets
        self.policy = policy
        self.reliability = reliability
        self.action_confirmation_ttl_seconds = max(1, action_confirmation_ttl_seconds)
        self.operations = BusinessOperations(
            store, tickets, policy, self.action_confirmation_ttl_seconds
        )

    def lookup_order(
        self,
        request: OrderLookupRequest,
        *,
        context: CustomerContext,
    ) -> OrderLookupResult:
        started = perf_counter()
        order = self.store.get_order(
            request.order_id, context.customer_id, tenant_id=context.tenant_id
        )
        self._audit(
            trace_id=context.trace_id,
            actor_id=context.actor_id,
            operation="order.get",
            resource_type="order",
            resource_id=request.order_id,
            outcome="success" if order else "not_found",
            started=started,
            tenant_id=context.tenant_id,
        )
        return OrderLookupResult(order=order)

    def refund_eligibility(
        self,
        request: OrderLookupRequest,
        *,
        context: CustomerContext,
    ) -> RefundEligibilityResult:
        order = self.store.get_order(
            request.order_id, context.customer_id, tenant_id=context.tenant_id
        )
        if order is None:
            return RefundEligibilityResult(False, "order_not_found")
        if not isfinite(order.refundable_amount) or order.refundable_amount <= 0:
            return RefundEligibilityResult(False, "order_not_refundable")
        return RefundEligibilityResult(
            True,
            "eligible",
            order.refundable_amount,
            self.policy.refund(order.refundable_amount),
        )

    def prepare_refund(
        self,
        request: RefundPreparationRequest,
        *,
        context: CustomerContext,
    ) -> RefundPreparationResult:
        prepared = self.operations.prepare(
            kind=PendingActionKind.REFUND,
            order_id=request.order_id,
            conversation_id=request.conversation_id,
            context=context,
        )
        eligibility = RefundEligibilityResult(
            prepared.reason is None,
            prepared.reason or "eligible",
            prepared.amount,
            prepared.policy,
        )
        return RefundPreparationResult(
            eligibility,
            pending=prepared.pending,
            ticket=prepared.ticket,
            operation=prepared.operation,
        )

    def prepare_return(
        self,
        request: ReturnPreparationRequest,
        *,
        context: CustomerContext,
    ) -> ReturnPreparationResult:
        prepared = self.operations.prepare(
            kind=PendingActionKind.RETURN_REQUEST,
            order_id=request.order_id,
            conversation_id=request.conversation_id,
            context=context,
        )
        return ReturnPreparationResult(
            pending=prepared.pending,
            ticket=prepared.ticket,
            operation=prepared.operation,
        )

    def resolve_action(
        self,
        request: ConfirmActionRequest,
        *,
        context: CustomerContext,
    ) -> OperationView:
        return self.operations.resolve(request.action_id, confirm=request.confirm, context=context)

    def submit_confirmed_action(
        self,
        request: ConfirmActionRequest,
        *,
        context: CustomerContext,
    ) -> OperationView:
        # Compatibility adapter; the whole durable resolution still runs atomically.
        if request.confirm is not True:
            raise ValueError("customer_confirmation_required")
        return self.resolve_action(request, context=context)

    def create_ticket(
        self,
        *,
        context: CustomerContext,
        conversation_id: str,
        reason: str,
        priority: TicketPriority = TicketPriority.NORMAL,
    ) -> TicketView:
        started = perf_counter()
        ticket = self.tickets.create_handoff(
            customer_id=context.customer_id,
            conversation_id=conversation_id,
            reason=reason,
            priority=priority,
            tenant_id=context.tenant_id,
        )
        self._audit(
            trace_id=context.trace_id,
            actor_id=context.actor_id,
            operation="ticket.create",
            resource_type="ticket",
            resource_id=ticket.ticket_id,
            outcome="success",
            started=started,
            details={"priority": priority.value},
            tenant_id=context.tenant_id,
        )
        return ticket

    def _audit(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        actor_id: str,
        operation: str,
        resource_type: str,
        resource_id: str | None,
        outcome: str,
        started: float,
        details: dict[str, object] | None = None,
    ) -> None:
        payload = dict(details or {})
        payload["latency_ms"] = round((perf_counter() - started) * 1000, 3)
        self.store.record_audit(
            trace_id=trace_id,
            actor_id=actor_id,
            operation=operation,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=payload,
            tenant_id=tenant_id,
        )
