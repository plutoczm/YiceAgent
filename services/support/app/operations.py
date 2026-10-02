"""Durable local operations; PostgreSQL locks order then operation, with CAS and uniqueness."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import Principal
from app.domain import (
    OperationStatus,
    OperationView,
    PendingActionKind,
    PendingActionView,
    PolicyAction,
    PolicyDecision,
    TicketPriority,
    TicketView,
)
from app.policy import ActionPolicy
from app.store import (
    ActionReceiptRow,
    ApprovalDecisionRow,
    AuditEventRow,
    OrderRow,
    PendingActionRow,
    SupportStore,
    TicketRow,
)
from app.tenancy import TenantId
from app.tickets import TicketWorkflow

ACTIVE = {OperationStatus.PENDING_CONFIRMATION, OperationStatus.PENDING_APPROVAL}
TERMINAL = set(OperationStatus) - ACTIVE
LEGAL_TRANSITIONS = {
    OperationStatus.PENDING_CONFIRMATION: {
        OperationStatus.PENDING_CONFIRMATION,
        OperationStatus.SUBMITTED,
        OperationStatus.CANCELLED,
        OperationStatus.EXPIRED,
        OperationStatus.INVALIDATED,
    },
    OperationStatus.PENDING_APPROVAL: {
        OperationStatus.PENDING_APPROVAL,
        OperationStatus.PENDING_CONFIRMATION,
        OperationStatus.SUBMITTED,
        OperationStatus.CANCELLED,
        OperationStatus.EXPIRED,
        OperationStatus.REJECTED,
        OperationStatus.INVALIDATED,
    },
}


class OperationConflict(ValueError):
    """Stale revision/contradictory decision; refresh and explicitly retry."""


@dataclass(frozen=True)
class CustomerContext:
    customer_id: str
    actor_id: str
    trace_id: str
    tenant_id: TenantId

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", TenantId(self.tenant_id))
        for name, limit in (("customer_id", 128), ("actor_id", 128), ("trace_id", 64)):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError("invalid_customer_context")


@dataclass(frozen=True)
class OperationPreparation:
    operation: OperationView | None
    pending: PendingActionView | None
    ticket: TicketView | None
    reason: str | None
    amount: float | None
    policy: PolicyDecision | None


def snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


class BusinessOperations:
    def __init__(
        self, store: SupportStore, tickets: TicketWorkflow, policy: ActionPolicy, ttl_seconds: int
    ) -> None:
        self.store, self.tickets, self.policy = store, tickets, policy
        self.ttl_seconds = max(1, ttl_seconds)

    def _snapshot(self, order: OrderRow, kind: PendingActionKind) -> tuple[dict, PolicyDecision]:
        if kind is PendingActionKind.REFUND:
            threshold = float(self.policy.refund_human_review_threshold)
            if not isfinite(threshold):
                raise ValueError("invalid_refund_policy_threshold")
            decision = self.policy.refund(order.refundable_amount)
        else:
            threshold, decision = None, self.policy.return_request()
        return {
            "snapshot_version": 1,
            "tenant_id": order.tenant_id,
            "customer_id": order.customer_id,
            "order_id": order.order_id,
            "kind": kind.value,
            "amount": order.refundable_amount if kind is PendingActionKind.REFUND else None,
            "threshold": threshold,
            "approval_required": decision.action is PolicyAction.REQUIRE_HUMAN,
            "delivered_required": kind is PendingActionKind.RETURN_REQUEST,
        }, decision

    @staticmethod
    def _eligibility(order: OrderRow | None, kind: PendingActionKind) -> str | None:
        if order is None:
            return "order_not_found"
        if kind is PendingActionKind.REFUND:
            if not isfinite(order.refundable_amount) or order.refundable_amount <= 0:
                return "order_not_refundable"
        elif order.status != "delivered":
            return "return_not_available"
        return None

    def _order(
        self, session: Session, *, tenant_id: str, customer_id: str, order_id: str | None
    ) -> OrderRow | None:
        return session.scalar(
            select(OrderRow)
            .where(
                OrderRow.tenant_id == tenant_id,
                OrderRow.customer_id == customer_id,
                OrderRow.order_id == order_id,
            )
            .with_for_update()
        )

    def _locked(
        self, session: Session, operation_id: str, *, tenant_id: str, customer_id: str | None
    ) -> tuple[PendingActionRow, OrderRow | None]:
        statement = select(PendingActionRow).where(
            PendingActionRow.tenant_id == TenantId(tenant_id),
            PendingActionRow.action_id == operation_id,
        )
        if customer_id is not None:
            statement = statement.where(PendingActionRow.customer_id == customer_id)
        initial = session.scalar(statement)
        if initial is None:
            raise ValueError("pending_action_not_found")
        order = self._order(
            session, tenant_id=tenant_id, customer_id=initial.customer_id, order_id=initial.order_id
        )
        # Refresh the identity-map row after waiting for another transaction.
        operation = session.scalar(
            statement.with_for_update().execution_options(populate_existing=True)
        )
        if operation is None:
            raise ValueError("pending_action_not_found")
        return operation, order

    def _approval(self, session: Session, row: PendingActionRow) -> ApprovalDecisionRow | None:
        return session.scalar(
            select(ApprovalDecisionRow).where(
                ApprovalDecisionRow.tenant_id == row.tenant_id,
                ApprovalDecisionRow.operation_id == row.action_id,
            )
        )

    def _receipt(self, session: Session, row: PendingActionRow) -> ActionReceiptRow | None:
        return session.scalar(
            select(ActionReceiptRow).where(
                ActionReceiptRow.tenant_id == row.tenant_id,
                ActionReceiptRow.operation_id == row.action_id,
                ActionReceiptRow.action_kind == row.kind,
            )
        )

    def _view(self, session: Session, row: PendingActionRow) -> OperationView:
        approval = self._approval(session, row) if row.approval_required else None
        return OperationView(
            operation_id=row.action_id,
            kind=PendingActionKind(row.kind),
            order_id=row.order_id,
            status=OperationStatus(row.status),
            version=row.version,
            snapshot_version=row.snapshot_version,
            quoted_amount=row.quoted_amount,
            customer_confirmed=row.confirmed_at is not None,
            requires_human_approval=row.approval_required,
            human_approved=approval is not None and approval.decision == "approve",
            ticket_id=row.ticket_id,
            expires_at=self.store._ensure_utc(row.expires_at),
            result=dict(row.result or {}),
        )

    def _audit(
        self,
        session: Session,
        *,
        context: CustomerContext,
        event: str,
        resource_type: str,
        resource_id: str | None,
        outcome: str,
        operation_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        session.add(
            AuditEventRow(
                event_id=f"AUD-{uuid4().hex[:12].upper()}",
                tenant_id=context.tenant_id,
                trace_id=context.trace_id,
                actor_id=context.actor_id,
                operation=event,
                operation_id=operation_id,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
                details=details or {},
                created_at=datetime.now(UTC),
            )
        )
        session.flush()

    def _change(
        self,
        session: Session,
        row: PendingActionRow,
        *,
        context: CustomerContext,
        status: OperationStatus,
        event: str,
        reason: str | None = None,
        **values,
    ) -> None:
        before = OperationStatus(row.status)
        if status not in LEGAL_TRANSITIONS.get(before, set()):
            raise OperationConflict("illegal_operation_transition")
        if status in TERMINAL:
            values["terminal_at"] = datetime.now(UTC)
        previous_version = row.version
        result = session.execute(
            update(PendingActionRow)
            .where(
                PendingActionRow.tenant_id == context.tenant_id,
                PendingActionRow.customer_id == row.customer_id,
                PendingActionRow.action_id == row.action_id,
                PendingActionRow.version == previous_version,
            )
            .values(status=status.value, version=previous_version + 1, **values)
        )
        if result.rowcount != 1:
            raise OperationConflict("operation_version_conflict")
        self._audit(
            session,
            context=context,
            event=event,
            resource_type="business_operation",
            resource_id=row.action_id,
            outcome=status.value,
            operation_id=row.action_id,
            details={
                "from": before.value,
                "to": status.value,
                "version": previous_version + 1,
                **({"reason": reason} if reason else {}),
            },
        )

    def _invalid_reason(
        self, session: Session, row: PendingActionRow, order: OrderRow | None
    ) -> str | None:
        kind = PendingActionKind(row.kind)
        reason = self._eligibility(order, kind)
        if reason:
            return reason
        if row.legacy:
            return "legacy_proposal_requires_reprepare"
        try:
            if snapshot_hash(row.snapshot) != row.snapshot_hash:
                return "authorization_snapshot_changed"
            current, _ = self._snapshot(order, kind)
            if row.payload.get("order_id") != row.order_id:
                return "authorization_snapshot_changed"
            if kind is PendingActionKind.REFUND and (
                type(row.payload.get("amount")) not in (int, float)
                or row.payload["amount"] != row.quoted_amount
                or order.refundable_amount != row.quoted_amount
            ):
                return "refund_proposal_changed"
            if current != row.snapshot or row.snapshot_version != current["snapshot_version"]:
                return "authorization_snapshot_changed"
            if row.approval_required != current["approval_required"]:
                return "authorization_snapshot_changed"
        except (ValueError, TypeError, AttributeError, KeyError):
            return "authorization_snapshot_changed"
        if row.confirmed_at is not None and row.confirmed_snapshot_hash != row.snapshot_hash:
            return "confirmation_snapshot_changed"
        approval = self._approval(session, row) if row.approval_required else None
        if approval is not None and (
            approval.snapshot_hash != row.snapshot_hash
            or approval.snapshot != row.snapshot
            or approval.snapshot_version != row.snapshot_version
        ):
            return "approval_snapshot_changed"
        field = order.refund_status if kind is PendingActionKind.REFUND else order.return_status
        return "order_request_already_recorded" if field == "requested" else None

    def _check_active(
        self,
        session: Session,
        row: PendingActionRow,
        order: OrderRow | None,
        context: CustomerContext,
    ) -> bool:
        if OperationStatus(row.status) in TERMINAL:
            return False
        if self.store._ensure_utc(row.expires_at) <= datetime.now(UTC):
            self._change(
                session,
                row,
                context=context,
                status=OperationStatus.EXPIRED,
                event="action.expire",
                result={"expired": True},
            )
            return False
        reason = self._invalid_reason(session, row, order)
        if reason:
            self._change(
                session,
                row,
                context=context,
                status=OperationStatus.INVALIDATED,
                event="operation.invalidated",
                reason=reason,
                result={"reason": reason},
            )
            return False
        return True

    def _prepared(self, session: Session, row: PendingActionRow) -> OperationPreparation:
        # A just-inserted ORM row may not have loaded nullable fields changed by scoped SQL.
        row = session.scalar(
            select(PendingActionRow)
            .where(
                PendingActionRow.tenant_id == row.tenant_id,
                PendingActionRow.customer_id == row.customer_id,
                PendingActionRow.action_id == row.action_id,
            )
            .execution_options(populate_existing=True)
        )
        ticket_row = (
            session.scalar(
                select(TicketRow).where(
                    TicketRow.tenant_id == row.tenant_id, TicketRow.ticket_id == row.ticket_id
                )
            )
            if row.ticket_id
            else None
        )
        decision = PolicyDecision(
            action=PolicyAction.REQUIRE_HUMAN
            if row.approval_required
            else PolicyAction.REQUIRE_CONFIRMATION,
            reasons=["persisted_operation_snapshot"],
        )
        return OperationPreparation(
            self._view(session, row),
            self.store._to_pending_action(row),
            self.store._to_ticket(ticket_row) if ticket_row else None,
            None,
            row.quoted_amount,
            decision,
        )

    def prepare(
        self,
        *,
        kind: PendingActionKind,
        order_id: str,
        conversation_id: str,
        context: CustomerContext,
    ) -> OperationPreparation:
        kind = PendingActionKind(kind)
        with self.store._session.begin() as session:
            order = self._order(
                session,
                tenant_id=context.tenant_id,
                customer_id=context.customer_id,
                order_id=order_id,
            )
            existing = session.scalar(
                select(PendingActionRow)
                .where(
                    PendingActionRow.tenant_id == context.tenant_id,
                    PendingActionRow.customer_id == context.customer_id,
                    PendingActionRow.order_id == order_id,
                    PendingActionRow.kind == kind.value,
                    PendingActionRow.status.in_([s.value for s in ACTIVE] + ["submitted"]),
                )
                .order_by(PendingActionRow.created_at, PendingActionRow.action_id)
                .with_for_update()
            )
            if order is not None and existing is not None:
                if existing.status == OperationStatus.SUBMITTED.value:
                    self._assert_receipt(session, existing)
                    return self._prepared(session, existing)
                if self._check_active(session, existing, order, context):
                    return self._prepared(session, existing)
            reason = self._eligibility(order, kind)
            if reason is None:
                field = (
                    order.refund_status if kind is PendingActionKind.REFUND else order.return_status
                )
                if field == "requested":
                    reason = "order_request_already_recorded"
            self._audit(
                session,
                context=context,
                event="refund.quote" if kind is PendingActionKind.REFUND else "order.get",
                resource_type="order",
                resource_id=order_id,
                outcome="success"
                if reason is None
                else "not_found"
                if order is None
                else "not_refundable",
                details={"amount": order.refundable_amount}
                if reason is None and kind is PendingActionKind.REFUND
                else {},
            )
            if reason:
                ticket = None
                if kind is PendingActionKind.RETURN_REQUEST:
                    ticket = self._ticket(
                        session,
                        context=context,
                        conversation_id=conversation_id,
                        reason=f"return_review:{order_id}",
                        priority=TicketPriority.NORMAL,
                    )
                return OperationPreparation(None, None, ticket, reason, None, None)
            snapshot, decision = self._snapshot(order, kind)
            if kind is PendingActionKind.REFUND:
                self._audit(
                    session,
                    context=context,
                    event="policy.refund",
                    resource_type="order",
                    resource_id=order_id,
                    outcome=decision.action.value,
                    details={"amount": order.refundable_amount, "reasons": decision.reasons},
                )
            now = datetime.now(UTC)
            row = PendingActionRow(
                action_id=f"ACT-{uuid4().hex[:12].upper()}",
                tenant_id=context.tenant_id,
                customer_id=context.customer_id,
                conversation_id=conversation_id,
                kind=kind.value,
                order_id=order_id,
                quoted_amount=snapshot["amount"],
                payload={
                    "order_id": order_id,
                    **({"amount": snapshot["amount"]} if kind is PendingActionKind.REFUND else {}),
                },
                snapshot=snapshot,
                snapshot_hash=snapshot_hash(snapshot),
                snapshot_version=1,
                version=1,
                legacy=False,
                approval_required=snapshot["approval_required"],
                status=(
                    OperationStatus.PENDING_APPROVAL
                    if snapshot["approval_required"]
                    else OperationStatus.PENDING_CONFIRMATION
                ).value,
                created_at=now,
                expires_at=now + timedelta(seconds=self.ttl_seconds),
            )
            session.add(row)
            session.flush()
            if row.approval_required:
                ticket = self._ticket(
                    session,
                    context=context,
                    conversation_id=conversation_id,
                    reason=f"high_value_refund:{order_id}:{row.quoted_amount}",
                    priority=TicketPriority.HIGH,
                    operation_id=row.action_id,
                )
                session.execute(
                    update(PendingActionRow)
                    .where(
                        PendingActionRow.tenant_id == context.tenant_id,
                        PendingActionRow.action_id == row.action_id,
                        PendingActionRow.version == 1,
                    )
                    .values(ticket_id=ticket.ticket_id)
                )
            self._audit(
                session,
                context=context,
                event="action.prepare",
                resource_type="pending_action",
                resource_id=row.action_id,
                operation_id=row.action_id,
                outcome="approval_required" if row.approval_required else "confirmation_required",
                details={
                    "kind": kind.value,
                    "expires_at": row.expires_at.isoformat(),
                    "ttl_backend": "database",
                    "snapshot_version": 1,
                    "version": 1,
                },
            )
            return self._prepared(session, row)

    def _ticket(
        self,
        session: Session,
        *,
        context: CustomerContext,
        conversation_id: str,
        reason: str,
        priority: TicketPriority,
        operation_id: str | None = None,
    ) -> TicketView:
        ticket = self.tickets.create_handoff(
            tenant_id=context.tenant_id,
            customer_id=context.customer_id,
            conversation_id=conversation_id,
            reason=reason,
            priority=priority,
            session=session,
        )
        self._audit(
            session,
            context=context,
            event="ticket.create",
            resource_type="ticket",
            resource_id=ticket.ticket_id,
            outcome="success",
            operation_id=operation_id,
            details={"priority": priority.value},
        )
        return ticket

    def resolve(
        self, operation_id: str, *, confirm: bool, context: CustomerContext
    ) -> OperationView:
        if type(confirm) is not bool:
            raise ValueError("explicit_confirmation_required")
        with self.store._session.begin() as session:
            row, order = self._locked(
                session, operation_id, tenant_id=context.tenant_id, customer_id=context.customer_id
            )
            if OperationStatus(row.status) in TERMINAL:
                self._assert_receipt(session, row)
                return self._view(session, row)
            if self.store._ensure_utc(row.expires_at) <= datetime.now(UTC):
                self._change(
                    session,
                    row,
                    context=context,
                    status=OperationStatus.EXPIRED,
                    event="action.expire",
                    result={"expired": True},
                )
            elif not confirm:
                self._change(
                    session,
                    row,
                    context=context,
                    status=OperationStatus.CANCELLED,
                    event="action.cancel",
                    result={"cancelled": True},
                )
            elif self._check_active(session, row, order, context):
                if row.confirmed_at is None:
                    self._change(
                        session,
                        row,
                        context=context,
                        status=OperationStatus(row.status),
                        event="operation.customer_confirmed",
                        confirmed_by=context.actor_id,
                        confirmed_at=datetime.now(UTC),
                        confirmed_snapshot_hash=row.snapshot_hash,
                    )
                self._submit_if_authorized(session, row, order, context)
            return self._view(session, row)

    def decide(
        self,
        operation_id: str,
        *,
        decision: str,
        expected_version: int,
        principal: Principal,
        trace_id: str,
    ) -> OperationView:
        principal.require_approver()
        if (
            decision not in {"approve", "reject"}
            or type(expected_version) is not int
            or expected_version < 1
        ):
            raise ValueError("invalid_approval_request")
        with self.store._session.begin() as session:
            row, order = self._locked(
                session, operation_id, tenant_id=principal.tenant_id, customer_id=None
            )
            context = CustomerContext(
                row.customer_id, principal.subject, trace_id, principal.tenant_id
            )
            previous = self._approval(session, row)
            if previous is not None and previous.decision != decision:
                raise OperationConflict("approval_decision_conflict")
            if OperationStatus(row.status) in TERMINAL:
                self._assert_receipt(session, row)
                return self._view(session, row)
            if not self._check_active(session, row, order, context):
                return self._view(session, row)
            if not row.approval_required:
                raise OperationConflict("approval_not_required")
            if previous is not None:
                return self._view(session, row)
            if row.version != expected_version:
                raise OperationConflict("operation_version_conflict")
            session.add(
                ApprovalDecisionRow(
                    tenant_id=principal.tenant_id,
                    operation_id=row.action_id,
                    decision=decision,
                    actor_id=principal.subject,
                    decided_at=datetime.now(UTC),
                    operation_version=row.version,
                    snapshot_version=row.snapshot_version,
                    snapshot_hash=row.snapshot_hash,
                    snapshot=dict(row.snapshot),
                )
            )
            session.flush()
            if decision == "reject":
                self._change(
                    session,
                    row,
                    context=context,
                    status=OperationStatus.REJECTED,
                    event="operation.rejected",
                    result={"rejected": True},
                )
            else:
                self._change(
                    session,
                    row,
                    context=context,
                    status=OperationStatus.PENDING_APPROVAL
                    if row.confirmed_at
                    else OperationStatus.PENDING_CONFIRMATION,
                    event="operation.approved",
                )
                self._submit_if_authorized(session, row, order, context)
            return self._view(session, row)

    def _submit_if_authorized(
        self, session: Session, row: PendingActionRow, order: OrderRow, context: CustomerContext
    ) -> None:
        if row.confirmed_at is None:
            return
        if row.approval_required:
            approval = self._approval(session, row)
            if approval is None or approval.decision != "approve":
                return
            if (
                approval.snapshot_hash != row.snapshot_hash
                or approval.snapshot != row.snapshot
                or approval.snapshot_version != row.snapshot_version
            ):
                self._change(
                    session,
                    row,
                    context=context,
                    status=OperationStatus.INVALIDATED,
                    event="operation.invalidated",
                    result={"reason": "approval_snapshot_changed"},
                )
                return
        if self._receipt(session, row) is not None:
            raise OperationConflict("unexpected_receipt_for_active_operation")
        self._checkpoint("before_order_update")
        values = (
            {"refund_status": "requested"}
            if row.kind == "refund"
            else {"return_status": "requested"}
        )
        changed = session.execute(
            update(OrderRow)
            .where(
                OrderRow.tenant_id == context.tenant_id,
                OrderRow.customer_id == context.customer_id,
                OrderRow.order_id == row.order_id,
            )
            .values(**values)
        )
        if changed.rowcount != 1:
            raise OperationConflict("order_update_conflict")
        self._checkpoint("after_order_update")
        result = {"order_id": row.order_id, **values}
        if row.kind == "refund":
            result["amount"] = row.quoted_amount
        session.add(
            ActionReceiptRow(
                idempotency_key=row.action_id,
                operation_id=row.action_id,
                legacy=False,
                tenant_id=context.tenant_id,
                action_kind=row.kind,
                result=result,
            )
        )
        session.flush()
        self._checkpoint("after_receipt")
        self._audit(
            session,
            context=context,
            event="refund.execute" if row.kind == "refund" else "return.execute",
            resource_type="order",
            resource_id=row.order_id,
            outcome="success",
            operation_id=row.action_id,
            details={"idempotency_key": row.action_id},
        )
        self._checkpoint("after_audit")
        self._change(
            session,
            row,
            context=context,
            status=OperationStatus.SUBMITTED,
            event="operation.submitted",
            result=result,
        )
        self._checkpoint("after_terminal_update")

    @staticmethod
    def _checkpoint(stage: str) -> None:
        """No-op test seam for precise transaction/crash and lock-order probes."""

    def _assert_receipt(self, session: Session, row: PendingActionRow) -> None:
        if row.status == OperationStatus.SUBMITTED.value:
            receipt = self._receipt(session, row)
            if receipt is None or receipt.result != row.result:
                raise OperationConflict("operation_receipt_inconsistent")

    def get(self, operation_id: str, *, principal: Principal) -> OperationView:
        is_agent = bool(principal.roles.intersection({"support_agent", "support_admin"}))
        with self.store._session() as session:
            statement = select(PendingActionRow).where(
                PendingActionRow.tenant_id == principal.tenant_id,
                PendingActionRow.action_id == operation_id,
            )
            if not is_agent:
                statement = statement.where(
                    PendingActionRow.customer_id == principal.require_customer()
                )
            row = session.scalar(statement)
            if row is None:
                raise ValueError("pending_action_not_found")
            self._assert_receipt(session, row)
            return self._view(session, row)

    def for_ticket(self, ticket_id: str, *, principal: Principal) -> OperationView:
        principal.require_agent()
        with self.store._session() as session:
            row = session.scalar(
                select(PendingActionRow).where(
                    PendingActionRow.tenant_id == principal.tenant_id,
                    PendingActionRow.ticket_id == ticket_id,
                )
            )
            if row is None:
                raise ValueError("operation_not_found")
            return self._view(session, row)

    def expire_due(self, *, tenant_id: str) -> int:
        tenant_id = TenantId(tenant_id)
        with self.store._session() as session:
            ids = session.scalars(
                select(PendingActionRow.action_id).where(
                    PendingActionRow.tenant_id == tenant_id,
                    PendingActionRow.status.in_([status.value for status in ACTIVE]),
                    PendingActionRow.expires_at <= datetime.now(UTC),
                )
            ).all()
        count = 0
        for operation_id in ids:
            with self.store._session.begin() as session:
                row, _ = self._locked(session, operation_id, tenant_id=tenant_id, customer_id=None)
                if OperationStatus(row.status) in ACTIVE and self.store._ensure_utc(
                    row.expires_at
                ) <= datetime.now(UTC):
                    context = CustomerContext(
                        row.customer_id, "system:expiry", "TRC-EXPIRY", tenant_id
                    )
                    self._change(
                        session,
                        row,
                        context=context,
                        status=OperationStatus.EXPIRED,
                        event="action.expire",
                        result={"expired": True},
                    )
                    count += 1
        return count
