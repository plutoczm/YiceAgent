"""Transactional v2 business execution; model candidates cannot confirm or approve."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from yice_agent.business.contracts import Approval, Confirmation, Draft, SimulationEvent
from yice_agent.business.models import (
    Action,
    Audit,
    CommandReceipt,
    Decision,
    Event,
    Order,
    Product,
    Receipt,
    Ticket,
    TicketMessage,
)
from yice_agent.business.rules import (
    PATHS,
    TICKET_TRANSITIONS,
    BusinessError,
    Policy,
    clean_text,
    digest,
    money,
    validate_order_action,
)
from yice_agent.identity import PERSONAS, Principal

ACTIVE = {"pending_confirmation", "pending_approval"}


def identifier(prefix: str) -> str:
    return prefix + "-" + uuid4().hex.upper()


class UnknownOutcome(BusinessError):
    def __init__(self, request_id: str, response: dict[str, Any] | None = None):
        super().__init__("unknown_outcome", 503)
        self.references = {"client_request_id": request_id}
        if response and response.get("action_id"):
            self.references["action_id"] = str(response["action_id"])


class BusinessService:
    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] | None = None,
        policy: Policy | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ):
        self.engine = engine
        self.sessions = sessionmaker(engine, expire_on_commit=False)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.policy = policy or Policy()
        self.fault_hook = fault_hook

    def now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("business_clock_must_be_aware")
        return value.astimezone(UTC)

    def _command(
        self,
        actor: Principal,
        request_id: str,
        command: str,
        payload: dict[str, Any],
        operation: Callable[[Session], dict[str, Any]],
    ) -> dict[str, Any]:
        fingerprint = digest({"command": command, "payload": payload})
        key = digest([actor.tenant_id, actor.subject, request_id])
        lock = int.from_bytes(bytes.fromhex(key[:16]), "big", signed=True)
        response = None
        commit_started = False
        new = False
        with self.sessions() as session:
            transaction = session.begin()
            try:
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
                existing = session.scalar(
                    select(CommandReceipt).where(
                        CommandReceipt.tenant == actor.tenant_id,
                        CommandReceipt.actor == actor.subject,
                        CommandReceipt.request_id == request_id,
                    )
                )
                if existing:
                    if existing.fingerprint != fingerprint:
                        raise BusinessError("request_id_conflict")
                    response = dict(existing.response)
                else:
                    response = operation(session)
                    session.flush()
                    if self.fault_hook:
                        self.fault_hook("before_commit")
                    session.add(
                        CommandReceipt(
                            id=identifier("CMD"),
                            tenant=actor.tenant_id,
                            actor=actor.subject,
                            request_id=request_id,
                            fingerprint=fingerprint,
                            response=response,
                            created_at=self.now(),
                        )
                    )
                    session.flush()
                    new = True
                commit_started = True
                transaction.commit()
            except BusinessError:
                if transaction.is_active:
                    transaction.rollback()
                raise
            except Exception:
                if transaction.is_active:
                    transaction.rollback()
                if commit_started:
                    raise UnknownOutcome(request_id, response) from None
                raise BusinessError("business_write_failed", 503) from None
        if new and self.fault_hook:
            try:
                self.fault_hook("after_commit")
            except Exception:
                raise UnknownOutcome(request_id, response) from None
        assert response is not None
        return response

    def _audit(
        self, session: Session, actor: Principal, event: str, resource: str, outcome: str
    ) -> None:
        session.add(
            Audit(
                id=identifier("AUD"),
                tenant=actor.tenant_id,
                actor=actor.subject,
                event=event,
                resource_id=resource,
                outcome=outcome,
                created_at=self.now(),
            )
        )

    def _order(
        self, session: Session, actor: Principal, object_id: str, *, lock: bool = False
    ) -> Order:
        query = select(Order).where(Order.id == object_id, Order.tenant == actor.tenant_id)
        if not actor.operator:
            query = query.where(Order.customer == actor.subject)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        value = session.scalar(query)
        if value is None:
            raise BusinessError("object_not_found", 404)
        return value

    def _ticket(
        self, session: Session, actor: Principal, object_id: str, *, lock: bool = False
    ) -> Ticket:
        query = select(Ticket).where(Ticket.id == object_id, Ticket.tenant == actor.tenant_id)
        if not actor.operator:
            query = query.where(Ticket.customer == actor.subject)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        value = session.scalar(query)
        if value is None:
            raise BusinessError("object_not_found", 404)
        return value

    @staticmethod
    def order_view(row: Order) -> dict[str, Any]:
        return {
            "order_id": row.id,
            "order_type": row.order_type,
            "customer_id": row.customer,
            "merchant_id": row.merchant,
            "status": row.status,
            "payment_status": row.payment_status,
            "refund_status": row.refund_status,
            "amount": money(row.total),
            "paid_amount": money(row.paid),
            "requested_refund_amount": money(row.refund_requested),
            "refunded_amount": money(row.refunded),
            "currency": "CNY",
            "items": row.items,
            "note": row.note,
            "version": row.version,
            "created_at": row.created_at.isoformat(),
        }

    @staticmethod
    def ticket_view(row: Ticket) -> dict[str, Any]:
        return {
            "ticket_id": row.id,
            "order_id": row.order_id,
            "customer_id": row.customer,
            "status": row.status,
            "assignee_id": row.assignee,
            "version": row.version,
        }

    def action_view(self, session: Session, row: Action) -> dict[str, Any]:
        decision = session.get(Decision, row.id)
        return {
            "action_id": row.id,
            "actor_id": row.actor,
            "kind": row.kind,
            "order_id": row.order_id,
            "ticket_id": row.ticket_id,
            "status": row.status,
            "version": row.version,
            "quoted_amount": money(row.quoted_amount) if row.quoted_amount is not None else None,
            "requires_approval": row.requires_approval,
            "human_approved": decision is not None and decision.decision == "approve",
            "confirmed": row.confirmed_at is not None,
            "expires_at": row.expires_at.isoformat(),
            "result": dict(row.result),
            "payload": dict(row.payload),
        }

    def products(self, actor: Principal) -> list[dict[str, Any]]:
        with self.sessions() as session:
            rows = session.scalars(
                select(Product)
                .where(Product.tenant == actor.tenant_id, Product.active.is_(True))
                .order_by(Product.id)
                .limit(100)
            )
            return [
                {
                    "product_id": p.id,
                    "order_type": p.order_type,
                    "merchant_id": p.merchant,
                    "name": p.name,
                    "price": money(p.price),
                    "version": p.version,
                }
                for p in rows
            ]

    def orders(self, actor: Principal) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(Order).where(Order.tenant == actor.tenant_id)
            if not actor.operator:
                query = query.where(Order.customer == actor.subject)
            return [
                self.order_view(row)
                for row in session.scalars(
                    query.order_by(Order.created_at.desc(), Order.id).limit(200)
                )
            ]

    def order(self, actor: Principal, object_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            return self.order_view(self._order(session, actor, object_id))

    def tickets(self, actor: Principal) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(Ticket).where(Ticket.tenant == actor.tenant_id)
            if not actor.operator:
                query = query.where(Ticket.customer == actor.subject)
            return [
                self.ticket_view(row)
                for row in session.scalars(
                    query.order_by(Ticket.created_at.desc(), Ticket.id).limit(200)
                )
            ]

    def messages(self, actor: Principal, ticket_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            self._ticket(session, actor, ticket_id)
            return [
                {
                    "message_id": m.id,
                    "sender_role": m.sender_role,
                    "sender_id": m.sender,
                    "message": m.body,
                    "created_at": m.created_at.isoformat(),
                }
                for m in session.scalars(
                    select(TicketMessage)
                    .where(
                        TicketMessage.tenant == actor.tenant_id,
                        TicketMessage.ticket_id == ticket_id,
                    )
                    .order_by(TicketMessage.created_at, TicketMessage.id)
                    .limit(200)
                )
            ]

    def actions(self, actor: Principal) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(Action).where(Action.tenant == actor.tenant_id)
            if not actor.operator:
                query = query.where(Action.actor == actor.subject)
            return [
                self.action_view(session, row)
                for row in session.scalars(
                    query.order_by(Action.created_at.desc(), Action.id).limit(200)
                )
            ]

    def action(self, actor: Principal, object_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            query = select(Action).where(Action.id == object_id, Action.tenant == actor.tenant_id)
            if not actor.operator:
                query = query.where(Action.actor == actor.subject)
            row = session.scalar(query)
            if row is None:
                raise BusinessError("object_not_found", 404)
            return self.action_view(session, row)

    def request_outcome(self, actor: Principal, request_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            row = session.scalar(
                select(CommandReceipt).where(
                    CommandReceipt.tenant == actor.tenant_id,
                    CommandReceipt.actor == actor.subject,
                    CommandReceipt.request_id == request_id,
                )
            )
            if row is None:
                raise BusinessError("request_outcome_not_recorded", 404)
            return {
                "client_request_id": request_id,
                "committed": True,
                "result": dict(row.response),
            }

    def _cart(
        self, session: Session, actor: Principal, payload: dict[str, Any], *, lock: bool = True
    ) -> tuple[list[dict[str, Any]], Decimal, str]:
        raw = payload["items"]
        ids = [item["product_id"] for item in raw]
        if len(set(ids)) != len(ids):
            raise BusinessError("duplicate_cart_product", 422)
        query = (
            select(Product)
            .where(Product.id.in_(ids), Product.tenant == actor.tenant_id, Product.active.is_(True))
            .order_by(Product.id)
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        rows = {row.id: row for row in session.scalars(query)}
        if len(rows) != len(ids):
            raise BusinessError("object_not_found", 404)
        merchants = {row.merchant for row in rows.values()}
        if len(merchants) != 1 or any(
            row.order_type != payload["order_type"] for row in rows.values()
        ):
            raise BusinessError("cart_scope_mismatch", 422)
        items = [
            {
                "product_id": item["product_id"],
                "name": rows[item["product_id"]].name,
                "quantity": item["quantity"],
                "unit_price": money(rows[item["product_id"]].price),
                "product_version": rows[item["product_id"]].version,
            }
            for item in raw
        ]
        total = sum(
            (Decimal(item["unit_price"]) * item["quantity"] for item in items), Decimal("0.00")
        )
        return items, total, next(iter(merchants))

    def _snapshot(
        self, session: Session, actor: Principal, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], Decimal | None, bool]:
        kind = payload["kind"]
        base: dict[str, Any] = {
            "policy_version": self.policy.version,
            "actor": actor.subject,
            "tenant": actor.tenant_id,
            "payload_hash": digest(payload),
        }
        amount = None
        approval = False
        if kind == "place_order":
            if actor.operator:
                raise BusinessError("customer_required", 403)
            items, amount, merchant = self._cart(session, actor, payload)
            base.update(items=items, amount=money(amount), merchant=merchant)
        elif kind in {"pay", "cancel_order", "refund"}:
            if actor.operator:
                raise BusinessError("customer_required", 403)
            order = self._order(session, actor, payload["order_id"], lock=True)
            validate_order_action(kind, order)
            amount = (
                order.total
                if kind == "pay"
                else order.paid
                if order.payment_status == "paid"
                else Decimal("0.00")
            )
            approval = kind in {"refund", "cancel_order"} and self.policy.approval(
                order.order_type, amount
            )
            base.update(
                order=self.order_view(order),
                threshold=money(self.policy.threshold(order.order_type)),
            )
        elif kind == "create_ticket":
            if actor.operator:
                raise BusinessError("customer_required", 403)
            if payload.get("order_id"):
                base["order"] = self.order_view(
                    self._order(session, actor, payload["order_id"], lock=True)
                )
        else:
            ticket = self._ticket(session, actor, payload["ticket_id"], lock=True)
            if ticket.status == "closed":
                raise BusinessError("ticket_closed")
            if kind == "assign_ticket":
                if not actor.operator:
                    raise BusinessError("operator_required", 403)
                assignee = PERSONAS.get(payload["assignee_id"])
                if (
                    assignee is None
                    or not assignee.operator
                    or assignee.tenant_id != actor.tenant_id
                ):
                    raise BusinessError("invalid_assignee", 422)
            elif kind == "transition_ticket":
                if not actor.operator:
                    raise BusinessError("operator_required", 403)
                if payload["status"] not in TICKET_TRANSITIONS.get(ticket.status, set()):
                    raise BusinessError("illegal_ticket_transition")
            base["ticket"] = self.ticket_view(ticket)
        base["approval_required"] = approval
        return base, amount, approval

    def propose(self, actor: Principal, request: Draft) -> dict[str, Any]:
        payload = request.model_dump(exclude={"client_request_id"})
        for field in ("message", "note"):
            if field in payload:
                payload[field] = clean_text(payload[field])

        def operation(session: Session) -> dict[str, Any]:
            snapshot, amount, approval = self._snapshot(session, actor, payload)
            ticket_id = payload.get("ticket_id")
            customer = (
                self._ticket(session, actor, ticket_id).customer if ticket_id else actor.subject
            )
            row = Action(
                id=identifier("ACT"),
                tenant=actor.tenant_id,
                actor=actor.subject,
                customer=customer,
                kind=payload["kind"],
                order_id=payload.get("order_id"),
                ticket_id=ticket_id,
                status="pending_approval" if approval else "pending_confirmation",
                payload=payload,
                snapshot=snapshot,
                snapshot_hash=digest(snapshot),
                quoted_amount=amount,
                requires_approval=approval,
                version=1,
                created_at=self.now(),
                expires_at=self.now() + timedelta(minutes=15),
                result={},
            )
            session.add(row)
            session.flush()
            self._audit(session, actor, "action.proposed", row.id, row.status)
            return self.action_view(session, row)

        return self._command(actor, request.client_request_id, "propose", payload, operation)

    def _lock_action(
        self, session: Session, actor: Principal, object_id: str, *, review: bool = False
    ) -> Action:
        query = select(Action).where(Action.id == object_id, Action.tenant == actor.tenant_id)
        if not review:
            query = query.where(Action.actor == actor.subject)
        initial = session.scalar(query)
        if initial is None:
            raise BusinessError("object_not_found", 404)
        if initial.order_id:
            self._order(session, actor, initial.order_id, lock=True)
        if initial.ticket_id:
            self._ticket(session, actor, initial.ticket_id, lock=True)
        if initial.kind == "place_order" and initial.status in ACTIVE:
            ids = [item["product_id"] for item in initial.payload["items"]]
            list(
                session.scalars(
                    select(Product)
                    .where(Product.tenant == actor.tenant_id, Product.id.in_(ids))
                    .order_by(Product.id)
                    .with_for_update()
                )
            )
        row = session.scalar(query.with_for_update().execution_options(populate_existing=True))
        assert row is not None
        return row

    def _active(self, session: Session, actor: Principal, row: Action) -> bool:
        if row.status not in ACTIVE:
            return False
        if (
            row.kind != row.payload.get("kind")
            or row.order_id != row.payload.get("order_id")
            and row.kind != "place_order"
            or row.ticket_id != row.payload.get("ticket_id")
            and row.kind not in {"create_ticket"}
        ):
            reason = "snapshot_changed"
        elif row.expires_at <= self.now():
            reason = "expired"
        else:
            reason = None
            try:
                snapshot, amount, approval = self._snapshot(
                    session, PERSONAS[row.actor], row.payload
                )
                if (
                    digest(row.snapshot) != row.snapshot_hash
                    or snapshot != row.snapshot
                    or amount != row.quoted_amount
                    or approval != row.requires_approval
                ):
                    reason = "snapshot_changed"
                decision = session.get(Decision, row.id)
                if decision and (
                    decision.snapshot_hash != row.snapshot_hash
                    or decision.tenant != row.tenant
                    or decision.operator not in PERSONAS
                    or not PERSONAS[decision.operator].operator
                    or PERSONAS[decision.operator].tenant_id != row.tenant
                ):
                    reason = "approval_snapshot_changed"
            except (BusinessError, ValueError, KeyError, TypeError):
                reason = "snapshot_changed"
        if reason:
            row.status = "expired" if reason == "expired" else "invalidated"
            row.version += 1
            row.result = {"reason": reason}
            self._audit(session, actor, "action.invalidated", row.id, row.status)
            session.flush()
            return False
        return True

    def review(self, actor: Principal, action_id: str, request: Approval) -> dict[str, Any]:
        if not actor.operator:
            raise BusinessError("operator_required", 403)

        def operation(session: Session) -> dict[str, Any]:
            row = self._lock_action(session, actor, action_id, review=True)
            if not self._active(session, actor, row):
                return self.action_view(session, row)
            if row.version != request.expected_version:
                raise BusinessError("action_version_conflict")
            if row.status != "pending_approval" or not row.requires_approval:
                raise BusinessError("approval_not_available")
            session.add(
                Decision(
                    action_id=row.id,
                    tenant=row.tenant,
                    operator=actor.subject,
                    decision=request.decision,
                    snapshot_hash=row.snapshot_hash,
                    created_at=self.now(),
                )
            )
            row.status = "pending_confirmation" if request.decision == "approve" else "rejected"
            row.version += 1
            self._audit(session, actor, "action.reviewed", row.id, row.status)
            session.flush()
            return self.action_view(session, row)

        return self._command(
            actor,
            request.client_request_id,
            "review",
            {"action_id": action_id, **request.model_dump(exclude={"client_request_id"})},
            operation,
        )

    def confirm(self, actor: Principal, action_id: str, request: Confirmation) -> dict[str, Any]:
        def operation(session: Session) -> dict[str, Any]:
            row = self._lock_action(session, actor, action_id)
            if row.status == "submitted":
                receipt = session.get(Receipt, row.id)
                if receipt is None or receipt.kind != row.kind or receipt.result != row.result:
                    raise BusinessError("receipt_integrity_failed", 503)
                return self.action_view(session, row)
            if not self._active(session, actor, row):
                return self.action_view(session, row)
            if row.version != request.expected_version:
                raise BusinessError("action_version_conflict")
            if not request.confirm:
                row.status = "cancelled"
                row.version += 1
                self._audit(session, actor, "action.cancelled", row.id, row.status)
                return self.action_view(session, row)
            if row.requires_approval:
                decision = session.get(Decision, row.id)
                if (
                    decision is None
                    or decision.decision != "approve"
                    or decision.snapshot_hash != row.snapshot_hash
                ):
                    raise BusinessError("approval_required")
            row.result = self._apply(session, actor, row)
            row.status = "submitted"
            row.version += 1
            row.confirmed_at = self.now()
            session.add(
                Receipt(
                    action_id=row.id,
                    tenant=row.tenant,
                    kind=row.kind,
                    result=dict(row.result),
                    created_at=self.now(),
                )
            )
            self._audit(session, actor, "action.submitted", row.id, row.status)
            session.flush()
            return self.action_view(session, row)

        return self._command(
            actor,
            request.client_request_id,
            "confirm",
            {"action_id": action_id, **request.model_dump(exclude={"client_request_id"})},
            operation,
        )

    def _apply(self, session: Session, actor: Principal, action: Action) -> dict[str, Any]:
        payload = action.payload
        kind = action.kind
        if kind == "place_order":
            snapshot = action.snapshot
            assert action.quoted_amount is not None
            order = Order(
                id=identifier("ECO" if payload["order_type"] == "ecommerce" else "FOOD"),
                tenant=actor.tenant_id,
                customer=actor.customer_id,
                merchant=snapshot["merchant"],
                order_type=payload["order_type"],
                status="awaiting_payment",
                payment_status="unpaid",
                refund_status="none",
                total=action.quoted_amount,
                paid=Decimal("0.00"),
                refund_requested=Decimal("0.00"),
                refunded=Decimal("0.00"),
                items=snapshot["items"],
                note=payload["note"],
                version=1,
                created_at=self.now(),
                updated_at=self.now(),
            )
            session.add(order)
            session.flush()
            action.order_id = order.id
            return {"order_id": order.id, "status": "order_created"}
        if kind in {"pay", "cancel_order", "refund"}:
            order = self._order(session, actor, payload["order_id"], lock=True)
            if kind == "pay":
                order.payment_status = "pending"
                status = "payment_requested"
            elif kind == "cancel_order":
                order.status = "cancelled"
                if order.payment_status == "paid":
                    order.refund_status = "requested"
                    order.refund_requested = order.paid
                status = (
                    "cancellation_and_refund_requested"
                    if order.payment_status == "paid"
                    else "order_cancelled"
                )
            else:
                order.refund_status = "requested"
                order.refund_requested = order.paid
                status = "refund_requested"
            order.version += 1
            order.updated_at = self.now()
            return {"order_id": order.id, "status": status, "order_version": order.version}
        if kind == "create_ticket":
            ticket = Ticket(
                id=identifier("TKT"),
                tenant=actor.tenant_id,
                customer=actor.customer_id,
                order_id=payload.get("order_id"),
                status="open",
                version=1,
                created_at=self.now(),
                updated_at=self.now(),
            )
            session.add(ticket)
            session.flush()
            action.ticket_id = ticket.id
        else:
            ticket = self._ticket(session, actor, payload["ticket_id"], lock=True)
        if kind in {"create_ticket", "reply_ticket"}:
            session.add(
                TicketMessage(
                    id=identifier("MSG"),
                    ticket_id=ticket.id,
                    tenant=actor.tenant_id,
                    sender=actor.subject,
                    sender_role="operator" if actor.operator else "customer",
                    body=payload["message"],
                    created_at=self.now(),
                )
            )
        elif kind == "assign_ticket":
            ticket.assignee = payload["assignee_id"]
            ticket.status = "assigned"
        elif kind == "transition_ticket":
            ticket.status = payload["status"]
        if kind != "create_ticket":
            ticket.version += 1
            ticket.updated_at = self.now()
        return {"ticket_id": ticket.id, "status": ticket.status, "ticket_version": ticket.version}

    def simulate(self, actor: Principal, request: SimulationEvent) -> dict[str, Any]:
        if not actor.operator:
            raise BusinessError("operator_required", 403)

        def operation(session: Session) -> dict[str, Any]:
            order = self._order(session, actor, request.order_id, lock=True)
            if order.version != request.expected_version:
                raise BusinessError("order_version_conflict")
            before = order.version
            if request.event in {"payment_succeeded", "payment_failed"}:
                if order.status != "awaiting_payment" or order.payment_status != "pending":
                    raise BusinessError("payment_event_not_available")
                if request.event == "payment_succeeded":
                    order.payment_status = "paid"
                    order.paid = order.total
                    order.status = "paid"
                else:
                    order.payment_status = "failed"
            elif request.event == "advance":
                path = PATHS[order.order_type]
                if (
                    order.payment_status != "paid"
                    or order.status not in path
                    or order.status in {"awaiting_payment", "delivered"}
                ):
                    raise BusinessError("delivery_event_not_available")
                order.status = path[path.index(order.status) + 1]
            else:
                if (
                    order.refund_status != "requested"
                    or order.refund_requested != order.paid
                    or order.paid <= 0
                    or order.refunded != 0
                ):
                    raise BusinessError("refund_event_not_available")
                if request.event == "refund_succeeded":
                    order.refund_status = "refunded"
                    order.refunded = order.refund_requested
                else:
                    order.refund_status = "failed"
            order.version += 1
            order.updated_at = self.now()
            session.add(
                Event(
                    id=identifier("SIM"),
                    tenant=actor.tenant_id,
                    order_id=order.id,
                    operator=actor.subject,
                    event=request.event,
                    before_version=before,
                    after_version=order.version,
                    created_at=self.now(),
                )
            )
            self._audit(session, actor, "simulator." + request.event, order.id, order.status)
            return self.order_view(order)

        return self._command(
            actor,
            request.client_request_id,
            "simulate",
            request.model_dump(exclude={"client_request_id"}),
            operation,
        )
