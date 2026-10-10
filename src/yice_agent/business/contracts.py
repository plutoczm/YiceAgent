"""Closed human-action DTOs; prices, identity, policy and state come from the server."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from yice_agent.contracts import Closed

RequestId = Annotated[
    str, Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")
]
ObjectId = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9-]{0,63}$")]
OrderType = Literal["ecommerce", "food"]


class Command(Closed):
    client_request_id: RequestId


class CartItem(Closed):
    product_id: ObjectId
    quantity: int = Field(ge=1, le=20)


class PlaceOrder(Command):
    kind: Literal["place_order"]
    order_type: OrderType
    items: list[CartItem] = Field(min_length=1, max_length=10)
    note: str = Field(default="", max_length=200)


class OrderAction(Command):
    kind: Literal["pay", "cancel_order", "refund"]
    order_id: ObjectId


class CreateTicket(Command):
    kind: Literal["create_ticket"]
    order_id: ObjectId | None = None
    message: str = Field(min_length=1, max_length=2000)


class ReplyTicket(Command):
    kind: Literal["reply_ticket"]
    ticket_id: ObjectId
    message: str = Field(min_length=1, max_length=2000)


class AssignTicket(Command):
    kind: Literal["assign_ticket"]
    ticket_id: ObjectId
    assignee_id: Literal["operator-a", "operator-b"]


class TransitionTicket(Command):
    kind: Literal["transition_ticket"]
    ticket_id: ObjectId
    status: Literal["pending_customer", "resolved", "closed", "assigned"]


Draft = Annotated[
    PlaceOrder | OrderAction | CreateTicket | ReplyTicket | AssignTicket | TransitionTicket,
    Field(discriminator="kind"),
]
DRAFT: TypeAdapter[Draft] = TypeAdapter(Draft)


class Confirmation(Command):
    confirm: bool
    expected_version: int = Field(ge=1)


class Approval(Command):
    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1)


class SimulationEvent(Command):
    order_id: ObjectId
    expected_version: int = Field(ge=1)
    event: Literal[
        "payment_succeeded", "payment_failed", "advance", "refund_succeeded", "refund_failed"
    ]


class PersonaSelection(Closed):
    persona: Literal[
        "customer-a1", "customer-a2", "operator-a", "customer-b1", "customer-b2", "operator-b"
    ]
