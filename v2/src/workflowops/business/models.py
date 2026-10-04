"""PostgreSQL-owned exact money, immutable approvals, receipts and metadata audit."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Version(Base):
    __tablename__ = "business_version"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    seed_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seed_complete: Mapped[bool] = mapped_column(Boolean, default=False)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64), index=True)
    merchant: Mapped[str] = mapped_column(String(64))
    order_type: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        CheckConstraint("price > 0 AND version > 0"),
        CheckConstraint("order_type IN ('ecommerce','food')"),
    )


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64), index=True)
    customer: Mapped[str] = mapped_column(String(64), index=True)
    merchant: Mapped[str] = mapped_column(String(64))
    order_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    payment_status: Mapped[str] = mapped_column(String(16), default="unpaid")
    refund_status: Mapped[str] = mapped_column(String(16), default="none")
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    refund_requested: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    refunded: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "total > 0 AND paid >= 0 AND paid <= total AND refund_requested >= 0 AND refund_requested <= paid AND refunded >= 0 AND refunded <= paid AND version > 0"
        ),
        CheckConstraint("order_type IN ('ecommerce','food')"),
        CheckConstraint("paid = 0 OR paid = total"),
        CheckConstraint("refunded = 0 OR refunded = paid"),
        CheckConstraint(
            "(order_type = 'ecommerce' AND status IN ('awaiting_payment','paid','ready_to_ship','shipped','delivered','cancelled')) OR (order_type = 'food' AND status IN ('awaiting_payment','paid','accepted','preparing','delivering','delivered','cancelled'))"
        ),
        CheckConstraint("payment_status IN ('unpaid','pending','paid','failed')"),
        CheckConstraint("refund_status IN ('none','requested','refunded','failed')"),
    )


class Action(Base):
    __tablename__ = "actions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    customer: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ticket_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    quoted_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (
        CheckConstraint("version > 0"),
        CheckConstraint(
            "status IN ('pending_confirmation','pending_approval','submitted','cancelled','expired','rejected','invalidated')"
        ),
    )


class Decision(Base):
    __tablename__ = "approval_decisions"
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id"), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64))
    operator: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16))
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Receipt(Base):
    __tablename__ = "action_receipts"
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id"), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CommandReceipt(Base):
    __tablename__ = "command_receipts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("tenant", "actor", "request_id"),)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64), index=True)
    customer: Mapped[str] = mapped_column(String(64))
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open")
    assignee: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TicketMessage(Base):
    __tablename__ = "ticket_messages"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
    tenant: Mapped[str] = mapped_column(String(64))
    sender: Mapped[str] = mapped_column(String(64))
    sender_role: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "simulation_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    operator: Mapped[str] = mapped_column(String(64))
    event: Mapped[str] = mapped_column(String(32))
    before_version: Mapped[int] = mapped_column(Integer)
    after_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Audit(Base):
    __tablename__ = "business_audit"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64))
    event: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
