from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain import TicketPriority, TicketStatus, TicketView
from app.store import SupportStore


class TicketWorkflow:
    _allowed: dict[TicketStatus, set[TicketStatus]] = {
        TicketStatus.OPEN: {TicketStatus.ASSIGNED},
        TicketStatus.ASSIGNED: {
            TicketStatus.PENDING_CUSTOMER,
            TicketStatus.RESOLVED,
            TicketStatus.OPEN,
        },
        TicketStatus.PENDING_CUSTOMER: {TicketStatus.ASSIGNED, TicketStatus.RESOLVED},
        TicketStatus.RESOLVED: {TicketStatus.CLOSED, TicketStatus.OPEN},
        TicketStatus.CLOSED: set(),
    }

    def __init__(self, store: SupportStore) -> None:
        self.store = store

    def create_handoff(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        conversation_id: str,
        reason: str,
        priority: TicketPriority = TicketPriority.NORMAL,
        session: Session | None = None,
    ) -> TicketView:
        return self.store.create_ticket(
            customer_id,
            conversation_id,
            reason,
            priority=priority,
            sla_due_at=self.store.sla_due(priority),
            session=session,
            tenant_id=tenant_id,
        )

    def assign(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        assignee_id: str,
        actor_id: str,
        trace_id: str,
    ) -> TicketView:
        ticket = self._require_ticket(ticket_id, tenant_id=tenant_id)
        if ticket.status in {TicketStatus.RESOLVED, TicketStatus.CLOSED}:
            raise ValueError("ticket_not_assignable")
        updated = self.store.assign_ticket(ticket_id, assignee_id, actor_id, tenant_id=tenant_id)
        self.store.record_audit(
            trace_id=trace_id,
            actor_id=actor_id,
            operation="ticket.assign",
            resource_type="ticket",
            resource_id=ticket_id,
            outcome="success",
            details={"assignee_id": assignee_id},
            tenant_id=tenant_id,
        )
        return updated

    def transition(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        target: TicketStatus,
        actor_id: str,
        trace_id: str,
        note: str | None,
    ) -> TicketView:
        ticket = self._require_ticket(ticket_id, tenant_id=tenant_id)
        if target == ticket.status:
            return ticket
        if target not in self._allowed[ticket.status]:
            raise ValueError(f"invalid_ticket_transition:{ticket.status.value}->{target.value}")
        if target is TicketStatus.RESOLVED and not ticket.assignee_id:
            raise ValueError("ticket_must_be_assigned_before_resolution")

        updated = self.store.transition_ticket(
            ticket_id, target, actor_id, note, tenant_id=tenant_id
        )
        self.store.record_audit(
            trace_id=trace_id,
            actor_id=actor_id,
            operation="ticket.transition",
            resource_type="ticket",
            resource_id=ticket_id,
            outcome="success",
            details={"from": ticket.status.value, "to": target.value, "note": note},
            tenant_id=tenant_id,
        )
        return updated

    def _require_ticket(self, ticket_id: str, *, tenant_id: str) -> TicketView:
        ticket = self.store.get_ticket(ticket_id, tenant_id=tenant_id)
        if ticket is None:
            raise KeyError(ticket_id)
        return ticket
