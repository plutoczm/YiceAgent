from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
    select,
    text,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.domain import (
    AuditEventView,
    OrderView,
    PendingActionKind,
    PendingActionStatus,
    PendingActionView,
    TicketMessageRole,
    TicketMessageView,
    TicketPriority,
    TicketStatus,
    TicketView,
)
from app.tenancy import TenantId


class Base(DeclarativeBase):
    pass


class OrderRow(Base):
    __tablename__ = "orders"

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        Index("ix_orders_tenant_customer", "tenant_id", "customer_id"),
        UniqueConstraint("tenant_id", "order_id", name="uq_order_tenant_id"),
    )

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32))
    amount: Mapped[float] = mapped_column(Float)
    refundable_amount: Mapped[float] = mapped_column(Float)
    refund_status: Mapped[str] = mapped_column(String(32), default="none")
    return_status: Mapped[str] = mapped_column(String(32), default="none")


class TicketRow(Base):
    __tablename__ = "tickets"

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "ticket_id", name="uq_ticket_tenant_id"),
        Index("ix_tickets_tenant_status_created", "tenant_id", "status", "created_at"),
        Index(
            "ix_tickets_tenant_customer_conversation",
            "tenant_id",
            "customer_id",
            "conversation_id",
            "created_at",
        ),
    )

    ticket_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(128), index=True)
    conversation_id: Mapped[str] = mapped_column(String(128), index=True)
    reason: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(32), default=TicketStatus.OPEN.value, index=True)
    priority: Mapped[str] = mapped_column(
        String(32),
        default=TicketPriority.NORMAL.value,
        index=True,
    )
    assignee_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    sla_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TicketEventRow(Base):
    __tablename__ = "ticket_events"

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[str] = mapped_column(String(128), index=True)
    from_status: Mapped[str] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TicketMessageRow(Base):
    __tablename__ = "ticket_messages"

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        Index("ix_ticket_messages_tenant_ticket_created", "tenant_id", "ticket_id", "created_at"),
    )

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(String(128), index=True)
    customer_id: Mapped[str] = mapped_column(String(128), index=True)
    sender_role: Mapped[str] = mapped_column(String(32), index=True)
    sender_id: Mapped[str] = mapped_column(String(128), index=True)
    body: Mapped[str] = mapped_column(String(8000))
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PendingActionRow(Base):
    __tablename__ = "pending_actions"

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_pending_actions_tenant_status_expiry", "tenant_id", "status", "expires_at"),
        UniqueConstraint("tenant_id", "action_id", name="uq_operation_tenant_id"),
        UniqueConstraint("tenant_id", "action_id", "kind", name="uq_operation_tenant_id_kind"),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.order_id"],
            name="fk_operation_order",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "ticket_id"],
            ["tickets.tenant_id", "tickets.ticket_id"],
            name="fk_operation_ticket",
        ),
        CheckConstraint(
            "status IN ('pending_confirmation','pending_approval','submitted',"
            "'cancelled','expired','rejected','invalidated')",
            name="ck_operation_status",
        ),
        CheckConstraint(
            "version >= 0 AND snapshot_version >= 0 AND "
            "(legacy OR (version >= 1 AND snapshot_version >= 1 AND order_id IS NOT NULL "
            "AND kind IN ('refund','return_request')))",
            name="ck_operation_identity",
        ),
        CheckConstraint(
            "legacy OR kind != 'refund' OR (quoted_amount IS NOT NULL AND quoted_amount > 0)",
            name="ck_operation_quote",
        ),
        CheckConstraint(
            "legacy OR ((confirmed_at IS NULL AND confirmed_by IS NULL "
            "AND confirmed_snapshot_hash IS NULL) OR (confirmed_at IS NOT NULL "
            "AND confirmed_by IS NOT NULL AND confirmed_snapshot_hash IS NOT NULL "
            "AND confirmed_snapshot_hash = snapshot_hash))",
            name="ck_operation_confirmation",
        ),
        CheckConstraint(
            "legacy OR status != 'submitted' OR (confirmed_at IS NOT NULL "
            "AND result IS NOT NULL AND terminal_at IS NOT NULL)",
            name="ck_operation_submission",
        ),
        CheckConstraint(
            "NOT legacy OR status NOT IN ('pending_confirmation','pending_approval')",
            name="ck_operation_legacy_terminal",
        ),
        Index(
            "uq_operation_business_intent",
            "tenant_id",
            "customer_id",
            "order_id",
            "kind",
            unique=True,
            postgresql_where=text(
                "NOT legacy AND status IN ('pending_confirmation','pending_approval','submitted')"
            ),
            sqlite_where=text(
                "NOT legacy AND status IN ('pending_confirmation','pending_approval','submitted')"
            ),
        ),
    )

    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quoted_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ticket_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(128), index=True)
    customer_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending_confirmation")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ActionReceiptRow(Base):
    __tablename__ = "action_receipts"

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)

    operation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "operation_id", "action_kind"],
            ["pending_actions.tenant_id", "pending_actions.action_id", "pending_actions.kind"],
            name="fk_receipt_operation",
        ),
        UniqueConstraint("tenant_id", "operation_id", name="uq_receipt_operation"),
        CheckConstraint(
            "legacy OR (operation_id IS NOT NULL AND operation_id = idempotency_key)",
            name="ck_receipt_identity",
        ),
    )

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    action_kind: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "operation_id"],
            ["pending_actions.tenant_id", "pending_actions.action_id"],
            name="fk_audit_operation",
        ),
        Index("ix_audit_events_tenant_trace_created", "tenant_id", "trace_id", "created_at"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[str] = mapped_column(String(128), index=True)
    operation: Mapped[str] = mapped_column(String(128), index=True)
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalDecisionRow(Base):
    __tablename__ = "operation_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "operation_id"],
            ["pending_actions.tenant_id", "pending_actions.action_id"],
            name="fk_approval_operation",
        ),
        CheckConstraint("decision IN ('approve','reject')", name="ck_approval_decision"),
        CheckConstraint(
            "operation_version >= 1 AND snapshot_version >= 1", name="ck_approval_version"
        ),
    )

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    operation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SupportStore:
    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, connect_args=connect_args)
        if database_url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def enable_foreign_keys(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")

        self._session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def seed_demo_data(self, *, tenant_id: str) -> None:
        tenant_id = TenantId(tenant_id)
        with self._session.begin() as session:
            exists = session.scalar(
                select(OrderRow.order_id).where(OrderRow.tenant_id == tenant_id).limit(1)
            )
            if exists:
                return
            session.add_all(
                [
                    OrderRow(
                        tenant_id=tenant_id,
                        order_id="ORD-1001",
                        customer_id="CUST-001",
                        status="delivered",
                        amount=199.0,
                        refundable_amount=199.0,
                    ),
                    OrderRow(
                        tenant_id=tenant_id,
                        order_id="ORD-1002",
                        customer_id="CUST-001",
                        status="shipped",
                        amount=89.0,
                        refundable_amount=0.0,
                    ),
                    OrderRow(
                        tenant_id=tenant_id,
                        order_id="ORD-2001",
                        customer_id="CUST-002",
                        status="delivered",
                        amount=899.0,
                        refundable_amount=899.0,
                    ),
                ]
            )

    def get_order(self, order_id: str, customer_id: str, *, tenant_id: str) -> OrderView | None:
        tenant_id = TenantId(tenant_id)
        with self._session() as session:
            row = session.scalar(
                select(OrderRow).where(
                    OrderRow.tenant_id == tenant_id,
                    OrderRow.order_id == order_id,
                    OrderRow.customer_id == customer_id,
                )
            )
            if row is None or row.customer_id != customer_id:
                return None
            return self._to_order(row)

    def create_ticket(
        self,
        customer_id: str,
        conversation_id: str,
        reason: str,
        *,
        tenant_id: str,
        priority: TicketPriority,
        sla_due_at: datetime,
        session: Session | None = None,
    ) -> TicketView:
        tenant_id = TenantId(tenant_id)
        ticket_id = f"TKT-{uuid4().hex[:12].upper()}"
        now = datetime.now(UTC)
        with nullcontext(session) if session is not None else self._session.begin() as session:
            row = TicketRow(
                tenant_id=tenant_id,
                ticket_id=ticket_id,
                customer_id=customer_id,
                conversation_id=conversation_id,
                reason=reason,
                status=TicketStatus.OPEN.value,
                priority=priority.value,
                sla_due_at=sla_due_at,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return self._to_ticket(row)

    def get_ticket(
        self, ticket_id: str, *, tenant_id: str, customer_id: str | None = None
    ) -> TicketView | None:
        tenant_id = TenantId(tenant_id)
        with self._session() as session:
            statement = select(TicketRow).where(
                TicketRow.tenant_id == tenant_id, TicketRow.ticket_id == ticket_id
            )
            if customer_id is not None:
                statement = statement.where(TicketRow.customer_id == customer_id)
            row = session.scalar(statement)
            return self._to_ticket(row) if row else None

    def find_latest_ticket_for_conversation(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        customer_id: str,
    ) -> TicketView | None:
        tenant_id = TenantId(tenant_id)
        with self._session() as session:
            statement = (
                select(TicketRow)
                .where(
                    TicketRow.tenant_id == tenant_id,
                    TicketRow.conversation_id == conversation_id,
                    TicketRow.customer_id == customer_id,
                )
                .order_by(TicketRow.created_at.desc())
                .limit(1)
            )
            row = session.scalar(statement)
            return self._to_ticket(row) if row else None

    def list_tickets(
        self, status: TicketStatus | None = None, *, tenant_id: str
    ) -> list[TicketView]:
        tenant_id = TenantId(tenant_id)
        with self._session() as session:
            statement = (
                select(TicketRow)
                .where(TicketRow.tenant_id == tenant_id)
                .order_by(TicketRow.created_at.desc())
            )
            if status is not None:
                statement = statement.where(TicketRow.status == status.value)
            return [self._to_ticket(row) for row in session.scalars(statement).all()]

    def assign_ticket(
        self, ticket_id: str, assignee_id: str, actor_id: str, *, tenant_id: str
    ) -> TicketView:
        tenant_id = TenantId(tenant_id)
        now = datetime.now(UTC)
        with self._session.begin() as session:
            row = session.scalar(
                select(TicketRow).where(
                    TicketRow.tenant_id == tenant_id, TicketRow.ticket_id == ticket_id
                )
            )
            if row is None:
                raise KeyError(ticket_id)
            previous = row.status
            session.execute(
                update(TicketRow)
                .where(TicketRow.tenant_id == tenant_id, TicketRow.ticket_id == ticket_id)
                .values(assignee_id=assignee_id, status=TicketStatus.ASSIGNED.value, updated_at=now)
            )
            session.add(
                TicketEventRow(
                    tenant_id=tenant_id,
                    event_id=f"TEV-{uuid4().hex[:12].upper()}",
                    ticket_id=ticket_id,
                    actor_id=actor_id,
                    from_status=previous,
                    to_status=TicketStatus.ASSIGNED.value,
                    note=f"assigned_to:{assignee_id}",
                    created_at=now,
                )
            )
        ticket = self.get_ticket(ticket_id, tenant_id=tenant_id)
        if ticket is None:
            raise RuntimeError("ticket_assignment_failed")
        return ticket

    def transition_ticket(
        self,
        ticket_id: str,
        target: TicketStatus,
        actor_id: str,
        note: str | None,
        *,
        tenant_id: str,
    ) -> TicketView:
        tenant_id = TenantId(tenant_id)
        now = datetime.now(UTC)
        with self._session.begin() as session:
            row = session.scalar(
                select(TicketRow).where(
                    TicketRow.tenant_id == tenant_id, TicketRow.ticket_id == ticket_id
                )
            )
            if row is None:
                raise KeyError(ticket_id)
            previous = row.status
            session.execute(
                update(TicketRow)
                .where(TicketRow.tenant_id == tenant_id, TicketRow.ticket_id == ticket_id)
                .values(status=target.value, updated_at=now)
            )
            session.add(
                TicketEventRow(
                    tenant_id=tenant_id,
                    event_id=f"TEV-{uuid4().hex[:12].upper()}",
                    ticket_id=ticket_id,
                    actor_id=actor_id,
                    from_status=previous,
                    to_status=target.value,
                    note=note,
                    created_at=now,
                )
            )
        ticket = self.get_ticket(ticket_id, tenant_id=tenant_id)
        if ticket is None:
            raise RuntimeError("ticket_transition_failed")
        return ticket

    def create_ticket_message(
        self,
        *,
        tenant_id: str,
        customer_id: str | None = None,
        ticket_id: str,
        sender_role: TicketMessageRole,
        sender_id: str,
        body: str,
        context: dict[str, Any] | None = None,
    ) -> TicketMessageView:
        tenant_id = TenantId(tenant_id)
        with self._session.begin() as session:
            statement = select(TicketRow).where(
                TicketRow.tenant_id == tenant_id, TicketRow.ticket_id == ticket_id
            )
            if customer_id is not None:
                statement = statement.where(TicketRow.customer_id == customer_id)
            ticket = session.scalar(statement)
            if ticket is None:
                raise KeyError(ticket_id)
            row = TicketMessageRow(
                tenant_id=tenant_id,
                message_id=f"TMSG-{uuid4().hex[:12].upper()}",
                ticket_id=ticket.ticket_id,
                conversation_id=ticket.conversation_id,
                customer_id=ticket.customer_id,
                sender_role=sender_role.value,
                sender_id=sender_id,
                body=body,
                context=context or {},
                created_at=datetime.now(UTC),
            )
            session.add(row)
        return self._to_ticket_message(row)

    def list_ticket_messages(
        self, ticket_id: str, *, tenant_id: str, customer_id: str | None = None
    ) -> list[TicketMessageView]:
        tenant_id = TenantId(tenant_id)
        with self._session() as session:
            statement = (
                select(TicketMessageRow)
                .where(
                    TicketMessageRow.tenant_id == tenant_id, TicketMessageRow.ticket_id == ticket_id
                )
                .order_by(TicketMessageRow.created_at.asc(), TicketMessageRow.message_id.asc())
            )
            if customer_id is not None:
                statement = statement.where(TicketMessageRow.customer_id == customer_id)
            return [self._to_ticket_message(row) for row in session.scalars(statement).all()]

    def get_pending_action(
        self, action_id: str, *, tenant_id: str, customer_id: str | None = None
    ) -> PendingActionView | None:
        tenant_id = TenantId(tenant_id)
        with self._session() as session:
            statement = select(PendingActionRow).where(
                PendingActionRow.tenant_id == tenant_id, PendingActionRow.action_id == action_id
            )
            if customer_id is not None:
                statement = statement.where(PendingActionRow.customer_id == customer_id)
            row = session.scalar(statement)
            if row is None:
                return None
            return self._to_pending_action(row)

    def record_audit(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        actor_id: str,
        operation: str,
        resource_type: str,
        resource_id: str | None,
        outcome: str,
        details: dict[str, Any] | None = None,
    ) -> AuditEventView:
        tenant_id = TenantId(tenant_id)
        row = AuditEventRow(
            tenant_id=tenant_id,
            event_id=f"AUD-{uuid4().hex[:12].upper()}",
            trace_id=trace_id,
            actor_id=actor_id,
            operation=operation,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=details or {},
            created_at=datetime.now(UTC),
        )
        with self._session.begin() as session:
            session.add(row)
        return self._to_audit(row)

    def get_trace_audit(self, trace_id: str, *, tenant_id: str) -> list[AuditEventView]:
        tenant_id = TenantId(tenant_id)
        with self._session() as session:
            statement = (
                select(AuditEventRow)
                .where(AuditEventRow.tenant_id == tenant_id, AuditEventRow.trace_id == trace_id)
                .order_by(AuditEventRow.created_at.asc())
            )
            return [self._to_audit(row) for row in session.scalars(statement).all()]

    @staticmethod
    def sla_due(priority: TicketPriority) -> datetime:
        durations = {
            TicketPriority.URGENT: timedelta(minutes=30),
            TicketPriority.HIGH: timedelta(hours=2),
            TicketPriority.NORMAL: timedelta(hours=8),
            TicketPriority.LOW: timedelta(hours=24),
        }
        return datetime.now(UTC) + durations[priority]

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @classmethod
    def _to_ticket(cls, row: TicketRow) -> TicketView:
        due = cls._ensure_utc(row.sla_due_at)
        created = cls._ensure_utc(row.created_at)
        updated = cls._ensure_utc(row.updated_at)
        status = TicketStatus(row.status)
        return TicketView(
            ticket_id=row.ticket_id,
            customer_id=row.customer_id,
            conversation_id=row.conversation_id,
            reason=row.reason,
            status=status,
            priority=TicketPriority(row.priority),
            assignee_id=row.assignee_id,
            sla_due_at=due,
            sla_breached=(
                status not in {TicketStatus.RESOLVED, TicketStatus.CLOSED}
                and due < datetime.now(UTC)
            ),
            created_at=created,
            updated_at=updated,
        )

    @classmethod
    def _to_pending_action(cls, row: PendingActionRow) -> PendingActionView:
        return PendingActionView(
            action_id=row.action_id,
            tenant_id=row.tenant_id,
            conversation_id=row.conversation_id,
            customer_id=row.customer_id,
            kind=PendingActionKind(row.kind),
            payload=dict(row.payload),
            status=PendingActionStatus(
                {"pending_confirmation": "pending", "submitted": "executed"}.get(
                    row.status, row.status
                )
            ),
            result=dict(row.result) if row.result else None,
            created_at=cls._ensure_utc(row.created_at),
            expires_at=cls._ensure_utc(row.expires_at),
        )

    @classmethod
    def _to_ticket_message(cls, row: TicketMessageRow) -> TicketMessageView:
        return TicketMessageView(
            message_id=row.message_id,
            ticket_id=row.ticket_id,
            conversation_id=row.conversation_id,
            customer_id=row.customer_id,
            sender_role=TicketMessageRole(row.sender_role),
            sender_id=row.sender_id,
            body=row.body,
            context=dict(row.context or {}),
            created_at=cls._ensure_utc(row.created_at),
        )

    @staticmethod
    def _to_order(row: OrderRow) -> OrderView:
        return OrderView(
            order_id=row.order_id,
            customer_id=row.customer_id,
            status=row.status,
            amount=row.amount,
            refundable_amount=row.refundable_amount,
            refund_status=row.refund_status,
            return_status=row.return_status,
        )

    @classmethod
    def _to_audit(cls, row: AuditEventRow) -> AuditEventView:
        return AuditEventView(
            event_id=row.event_id,
            trace_id=row.trace_id,
            actor_id=row.actor_id,
            operation=row.operation,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            outcome=row.outcome,
            details=dict(row.details),
            created_at=cls._ensure_utc(row.created_at),
        )
