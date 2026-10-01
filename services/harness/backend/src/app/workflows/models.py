"""Closed P3 workflow request and result contracts."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.adapters.supportops_dto import (
    ConfirmationResponse,
    OperationView,
    OrderView,
    SupportResponse,
    TicketMessageView,
    TicketView,
)

ID = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class Closed(BaseModel):
    """Reject unsupported caller fields."""

    model_config = ConfigDict(extra="forbid")


class Lookup(Closed):
    """Read an owned order or operation."""

    kind: Literal["order", "operation"] = "order"
    object_id: ID


class Message(Closed):
    """Explicit action or ordinary grounded customer message."""

    action: Literal["message", "refund", "handoff"] = "message"
    conversation_id: ID
    message: str = Field(default="Please arrange human support", min_length=1, max_length=8000)
    order_id: str | None = Field(default=None, pattern=r"^ORD-[0-9]{4,12}$")

    @model_validator(mode="after")
    def require_order(self) -> "Message":
        """Make the refund action independent of language extraction."""
        if self.action == "refund" and not self.order_id:
            raise ValueError("order_id_required")
        return self


class Confirm(Closed):
    """Customer decision; never implied by an operator approval."""

    action_id: ID
    confirm: bool = Field(strict=True)


class Review(Closed):
    """Versioned operator decision."""

    operation_id: ID
    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1, strict=True)


class Ticket(Closed):
    """Finite ticket actions with role-dependent routes."""

    action: Literal["list", "get", "messages", "reply", "assign", "transition", "operation"]
    ticket_id: ID | None = None
    message: str | None = Field(default=None, min_length=1, max_length=8000)
    status: Literal["open", "assigned", "pending_customer", "resolved", "closed"] | None = None

    @model_validator(mode="after")
    def check_action(self) -> "Ticket":
        """Require precisely the fields needed by each ticket action."""
        if (self.action == "list") != (self.ticket_id is None):
            raise ValueError("ticket_id_mismatch")
        if (self.action == "reply") != (self.message is not None):
            raise ValueError("message_mismatch")
        if (self.action == "transition") != (self.status is not None):
            raise ValueError("status_mismatch")
        return self


Payload = Lookup | Message | Confirm | Review | Ticket
REQUESTS: dict[str, type[Payload]] = {
    "support_lookup": Lookup,
    "support_message": Message,
    "support_confirm": Confirm,
    "support_review": Review,
    "ticket_action": Ticket,
}


class RunRequest(Closed):
    """Outer request with optional ownership-checked parent."""

    payload: dict[str, object]
    parent_run_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")] | None = None


class Status(StrEnum):
    """Public terminal states of a synchronous run."""

    COMPLETED = "completed"
    CONFIRMATION = "waiting_confirmation"
    APPROVAL = "waiting_approval"
    FAILED = "failed"
    UNKNOWN = "unknown_outcome"


class Error(StrEnum):
    """Safe error categories without downstream bodies or exception text."""

    AUTHORIZATION = "authorization_denied"
    REJECTED = "domain_rejected"
    TIMEOUT = "downstream_timeout"
    UNAVAILABLE = "downstream_unavailable"
    INVALID_RESPONSE = "invalid_domain_response"
    INTERNAL = "internal_error"


class Step(Closed):
    """Actual graph node execution metadata."""

    name: Literal["validate", "authorize", "invoke_domain", "format_record"]
    status: Literal["completed", "failed", "skipped"]
    elapsed_ms: float


class RunRecord(Closed):
    """Persisted metadata excludes all domain result rows and text."""

    run_id: str
    parent_run_id: str | None
    workflow_id: str
    status: Status
    steps: list[Step]
    domain_refs: dict[str, str]
    error: Error | None = None
    mode: Literal["offline"] = "offline"


class RunResponse(RunRecord):
    """Typed public result; the durable RunRecord deliberately omits it."""

    result: (
        OrderView
        | OperationView
        | SupportResponse
        | ConfirmationResponse
        | TicketView
        | TicketMessageView
        | list[TicketView]
        | list[TicketMessageView]
        | None
    ) = None
