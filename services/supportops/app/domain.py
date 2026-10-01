from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Intent(StrEnum):
    KNOWLEDGE = "knowledge"
    ORDER_STATUS = "order_status"
    REFUND = "refund"
    RETURN_REQUEST = "return_request"
    COMPLAINT = "complaint"
    UNKNOWN = "unknown"


class PolicyAction(StrEnum):
    ALLOW = "allow"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REQUIRE_HUMAN = "require_human"


class PendingActionKind(StrEnum):
    REFUND = "refund"
    RETURN_REQUEST = "return_request"


class PendingActionStatus(StrEnum):
    PENDING = "pending"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


class TicketStatus(StrEnum):
    OPEN = "open"
    ASSIGNED = "assigned"
    PENDING_CUSTOMER = "pending_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(StrEnum):
    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class TicketMessageRole(StrEnum):
    CUSTOMER = "customer"
    ASSISTANT = "assistant"
    AGENT = "agent"


class SupportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=8000)


class RoutingDecision(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    source: str = "rules"


class GuardrailAssessment(BaseModel):
    blocked: bool
    sanitized_text: str
    labels: list[str] = Field(default_factory=list)
    redactions: list[str] = Field(default_factory=list)


class KnowledgeCitation(BaseModel):
    document_id: str
    title: str
    snippet: str
    score: float = Field(ge=0.0)
    source_uri: str | None = None
    document_version: str | None = None
    chunk_id: str | None = None


class OrderView(BaseModel):
    order_id: str
    customer_id: str
    status: str
    amount: float
    refundable_amount: float
    refund_status: str
    return_status: str


class TicketView(BaseModel):
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
    message_id: str
    ticket_id: str
    conversation_id: str
    customer_id: str
    sender_role: TicketMessageRole
    sender_id: str
    body: str
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class PendingActionView(BaseModel):
    tenant_id: str = Field(exclude=True)
    action_id: str
    conversation_id: str
    customer_id: str
    kind: PendingActionKind
    payload: dict[str, Any]
    status: PendingActionStatus
    result: dict[str, Any] | None = None
    created_at: datetime
    expires_at: datetime


class PolicyDecision(BaseModel):
    action: PolicyAction
    reasons: list[str] = Field(default_factory=list)


class SupportResponse(BaseModel):
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


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = Field(strict=True)


class ConfirmationResponse(BaseModel):
    operation_status: str | None = None
    trace_id: str
    action_id: str
    status: PendingActionStatus
    result: dict[str, Any]


class TicketAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignee_id: str = Field(min_length=1, max_length=128)


class TicketTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TicketStatus
    note: str | None = Field(default=None, max_length=1000)


class TicketMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=8000)


class AuditEventView(BaseModel):
    event_id: str
    trace_id: str
    actor_id: str
    operation: str
    resource_type: str
    resource_id: str | None
    outcome: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class OperationStatus(StrEnum):
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


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1, strict=True)
