"""P3 HTTP wire snapshot generated from domain DTOs; no cross-service imports."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Intent(StrEnum):
    """SupportOps v1 Intent wire contract."""

    KNOWLEDGE = "knowledge"
    ORDER_STATUS = "order_status"
    REFUND = "refund"
    RETURN_REQUEST = "return_request"
    COMPLAINT = "complaint"
    UNKNOWN = "unknown"


class PendingActionKind(StrEnum):
    """SupportOps v1 PendingActionKind wire contract."""

    REFUND = "refund"
    RETURN_REQUEST = "return_request"


class PendingActionStatus(StrEnum):
    """SupportOps v1 PendingActionStatus wire contract."""

    PENDING = "pending"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


class TicketStatus(StrEnum):
    """SupportOps v1 TicketStatus wire contract."""

    OPEN = "open"
    ASSIGNED = "assigned"
    PENDING_CUSTOMER = "pending_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(StrEnum):
    """SupportOps v1 TicketPriority wire contract."""

    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class TicketMessageRole(StrEnum):
    """SupportOps v1 TicketMessageRole wire contract."""

    CUSTOMER = "customer"
    ASSISTANT = "assistant"
    AGENT = "agent"


class KnowledgeCitation(BaseModel):
    """SupportOps v1 KnowledgeCitation wire contract."""

    document_id: str
    title: str
    snippet: str
    score: float = Field(ge=0.0)
    source_uri: str | None = None
    document_version: str | None = None
    chunk_id: str | None = None


class OrderView(BaseModel):
    """SupportOps v1 OrderView wire contract."""

    order_id: str
    customer_id: str
    status: str
    amount: float
    refundable_amount: float
    refund_status: str
    return_status: str


class TicketView(BaseModel):
    """SupportOps v1 TicketView wire contract."""

    ticket_id: str
    customer_id: str
    conversation_id: str
    reason: str
    status: TicketStatus
    priority: TicketPriority
    assignee_id: str | None = None
    sla_due_at: datetime
    sla_breached: bool
    created_at: datetime
    updated_at: datetime


class TicketMessageView(BaseModel):
    """SupportOps v1 TicketMessageView wire contract."""

    message_id: str
    ticket_id: str
    conversation_id: str
    customer_id: str
    sender_role: TicketMessageRole
    sender_id: str
    body: str
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class SupportResponse(BaseModel):
    """SupportOps v1 SupportResponse wire contract."""

    trace_id: str
    intent: Intent
    answer: str
    citations: list[KnowledgeCitation] = Field(default_factory=list)
    pending_action_id: str | None = None
    pending_action_expires_at: datetime | None = None
    ticket_id: str | None = None
    operation_id: str | None = None
    operation_status: str | None = None
    handoff: bool = False
    safety_labels: list[str] = Field(default_factory=list)
    retrieval_degraded: bool = False
    retrieval_degradation_reason: str | None = None


class ConfirmationResponse(BaseModel):
    """SupportOps v1 ConfirmationResponse wire contract."""

    operation_status: str | None = None
    trace_id: str
    action_id: str
    status: PendingActionStatus
    result: dict[str, Any]


class OperationStatus(StrEnum):
    """SupportOps v1 OperationStatus wire contract."""

    PENDING_CONFIRMATION = "pending_confirmation"
    PENDING_APPROVAL = "pending_approval"
    SUBMITTED = "submitted"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


class OperationView(BaseModel):
    """Customer-safe business state; no employee identity or internal audit details."""

    operation_id: str
    kind: PendingActionKind
    order_id: str | None
    status: OperationStatus
    version: int
    snapshot_version: int
    quoted_amount: float | None
    customer_confirmed: bool
    requires_human_approval: bool
    human_approved: bool
    ticket_id: str | None
    expires_at: datetime
    result: dict[str, Any] = Field(default_factory=dict)
