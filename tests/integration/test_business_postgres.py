"""Actual PostgreSQL transactions and concurrent domain state, not SQLite substitutes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yice_agent.business.bootstrap import ready, seed
from yice_agent.business.contracts import DRAFT, Approval, Confirmation, SimulationEvent
from yice_agent.business.models import (
    Action,
    Audit,
    Event,
    Order,
    Product,
    Receipt,
    Ticket,
)
from yice_agent.business.rules import BusinessError, Policy
from yice_agent.business.service import UnknownOutcome
from yice_agent.identity import PERSONAS

pytestmark = pytest.mark.integration
A = PERSONAS["customer-a1"]
A2 = PERSONAS["customer-a2"]
B = PERSONAS["customer-b1"]
OP = PERSONAS["operator-a"]
OPB = PERSONAS["operator-b"]


def key():
    return str(uuid4())


def propose(service, actor=A, **body):
    return service.propose(actor, DRAFT.validate_python({"client_request_id": key(), **body}))


def confirm(service, row, actor=A, accept=True, request_id=None):
    return service.confirm(
        actor,
        row["action_id"],
        Confirmation(
            client_request_id=request_id or key(), confirm=accept, expected_version=row["version"]
        ),
    )


def simulate(service, order_id, event, actor=OP, request_id=None):
    row = service.order(actor, order_id)
    return service.simulate(
        actor,
        SimulationEvent(
            client_request_id=request_id or key(),
            order_id=order_id,
            event=event,
            expected_version=row["version"],
        ),
    )


def new_order(service, family="ecommerce", sku="BASE", quantity=1, actor=A):
    prefix = "ECO" if family == "ecommerce" else "FOOD"
    draft = propose(
        service,
        actor,
        kind="place_order",
        order_type=family,
        items=[
            {
                "product_id": prefix + "-" + actor.tenant_id[-1].upper() + "-" + sku,
                "quantity": quantity,
            }
        ],
        note="",
    )
    result = confirm(service, draft, actor)
    return result["result"]["order_id"]


def pay(service, order_id, actor=A, operator=OP):
    draft = propose(service, actor, kind="pay", order_id=order_id)
    confirm(service, draft, actor)
    assert service.order(actor, order_id)["payment_status"] == "pending"
    return simulate(service, order_id, "payment_succeeded", operator)


def count(service, model):
    with Session(service.engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def test_seed_is_complete_repeatable_and_no_op(business):
    service, schema = business
    assert ready(service.engine)
    assert count(service, Order) == 24
    assert count(service, Product) == 16
    before = {
        model.__tablename__: count(service, model) for model in [Order, Action, Event, Receipt]
    }
    result = seed(service.engine, schema)
    assert result["status"] == "no_op"
    assert before == {
        model.__tablename__: count(service, model) for model in [Order, Action, Event, Receipt]
    }
    for actor in [A, A2, B, OPB]:
        assert (
            all(row["customer_id"] == actor.subject for row in service.orders(actor))
            if not actor.operator
            else True
        )


@pytest.mark.parametrize("family", ["ecommerce", "food"])
def test_create_pay_full_delivery_and_refund(business, family):
    service, _ = business
    order_id = new_order(service, family, "TEA" if family == "food" else "CABLE", 3)
    assert service.order(A, order_id)["amount"] == "59.70"
    pay(service, order_id)
    for _ in range(4 if family == "food" else 3):
        simulate(service, order_id, "advance")
    assert service.order(A, order_id)["status"] == "delivered"
    with pytest.raises(BusinessError, match="cancellation_not_available"):
        propose(service, kind="cancel_order", order_id=order_id)
    draft = propose(service, kind="refund", order_id=order_id)
    assert draft["quoted_amount"] == "59.70" and not draft["requires_approval"]
    confirm(service, draft)
    row = service.order(A, order_id)
    assert row["refund_status"] == "requested" and row["refunded_amount"] == "0.00"
    simulate(service, order_id, "refund_succeeded")
    row = service.order(A, order_id)
    assert (
        row["status"] == "delivered"
        and row["refund_status"] == "refunded"
        and row["refunded_amount"] == "59.70"
    )
    with pytest.raises(BusinessError, match="refund_already_recorded"):
        propose(service, kind="refund", order_id=order_id)


@pytest.mark.parametrize(
    "family,quantity,needs_review",
    [("ecommerce", 5, False), ("ecommerce", 6, True), ("food", 1, False), ("food", 2, True)],
)
def test_high_refund_independent_operator_then_customer(business, family, quantity, needs_review):
    service, _ = business
    order_id = new_order(service, family, quantity=quantity)
    pay(service, order_id)
    draft = propose(service, kind="refund", order_id=order_id)
    assert draft["requires_approval"] is needs_review
    if needs_review:
        with pytest.raises(BusinessError, match="approval_required"):
            confirm(service, draft)
        with pytest.raises(BusinessError, match="operator_required"):
            service.review(
                A,
                draft["action_id"],
                Approval(client_request_id=key(), decision="approve", expected_version=1),
            )
        request = Approval(client_request_id=key(), decision="approve", expected_version=1)
        approved = service.review(OP, draft["action_id"], request)
        assert approved == service.review(OP, draft["action_id"], request)
        assert approved["status"] == "pending_confirmation"
        with pytest.raises(BusinessError, match="object_not_found"):
            confirm(service, approved, OP)
        draft = approved
    result = confirm(service, draft)
    assert result["status"] == "submitted" and result["confirmed"]
    assert service.order(A, order_id)["refund_status"] == "requested"


@pytest.mark.parametrize("family", ["ecommerce", "food"])
def test_unpaid_and_paid_cancellation_atomic(business, family):
    service, _ = business
    order_id = new_order(service, family)
    action = propose(service, kind="cancel_order", order_id=order_id)
    confirm(service, action)
    assert service.order(A, order_id)["status"] == "cancelled"
    assert service.order(A, order_id)["refund_status"] == "none"
    order_id = new_order(service, family, "TEA" if family == "food" else "CABLE")
    pay(service, order_id)
    action = propose(service, kind="cancel_order", order_id=order_id)
    confirm(service, action)
    order = service.order(A, order_id)
    assert order["status"] == "cancelled" and order["refund_status"] == "requested"
    with pytest.raises(BusinessError, match="delivery_event_not_available"):
        simulate(service, order_id, "advance")
    simulate(service, order_id, "refund_succeeded")
    assert service.order(A, order_id)["refunded_amount"] == order["paid_amount"]


def test_rejected_and_cancelled_drafts_do_not_mutate_order(business):
    service, _ = business
    order_id = new_order(service, "ecommerce", "PHONE")
    pay(service, order_id)
    draft = propose(service, kind="refund", order_id=order_id)
    rejected = service.review(
        OP,
        draft["action_id"],
        Approval(client_request_id=key(), decision="reject", expected_version=1),
    )
    assert confirm(service, rejected)["status"] == "rejected"
    again = propose(service, kind="refund", order_id=order_id)
    assert confirm(service, again, accept=False)["status"] == "cancelled"
    assert service.order(A, order_id)["refund_status"] == "none"


def test_payment_failure_and_refund_failure_require_new_confirmed_intent(business):
    service, _ = business
    order_id = new_order(service, "food", "TEA")
    first = propose(service, kind="pay", order_id=order_id)
    confirm(service, first)
    simulate(service, order_id, "payment_failed")
    assert service.order(A, order_id)["payment_status"] == "failed"
    pay(service, order_id)
    first = propose(service, kind="refund", order_id=order_id)
    confirm(service, first)
    simulate(service, order_id, "refund_failed")
    second = propose(service, kind="refund", order_id=order_id)
    assert second["action_id"] != first["action_id"]
    assert service.order(A, order_id)["refunded_amount"] == "0.00"
    confirm(service, second)
    simulate(service, order_id, "refund_succeeded")


def test_cross_customer_tenant_and_operator_simulation_denied(business):
    service, _ = business
    for actor in [A2, B, OPB]:
        with pytest.raises(BusinessError, match="object_not_found"):
            service.order(actor, "ECO-A-01")
    with pytest.raises(BusinessError, match="operator_required"):
        simulate(service, "ECO-A-01", "payment_succeeded", A)
    with pytest.raises(BusinessError, match="object_not_found"):
        propose(
            service,
            kind="place_order",
            order_type="food",
            items=[{"product_id": "FOOD-B-TEA", "quantity": 1}],
            note="",
        )
    with pytest.raises(BusinessError, match="cart_scope_mismatch"):
        propose(
            service,
            kind="place_order",
            order_type="food",
            items=[{"product_id": "ECO-A-CABLE", "quantity": 1}],
            note="",
        )


def test_request_id_reuse_conflict_and_concurrent_place_order(business):
    service, _ = business
    body = DRAFT.validate_python(
        {
            "kind": "place_order",
            "order_type": "food",
            "items": [{"product_id": "FOOD-A-TEA", "quantity": 1}],
            "note": "",
            "client_request_id": key(),
        }
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.propose(A, body), range(4)))
    assert len({row["action_id"] for row in results}) == 1
    row = results[0]
    command = Confirmation(confirm=True, expected_version=row["version"], client_request_id=key())
    before = count(service, Order)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.confirm(A, row["action_id"], command), range(4)))
    assert len({value["result"]["order_id"] for value in results}) == 1
    assert count(service, Order) == before + 1
    modified = body.model_copy(update={"note": "changed"})
    with pytest.raises(BusinessError, match="request_id_conflict"):
        service.propose(A, modified)


def test_two_refunds_or_cancel_refund_cannot_double_submit(business):
    service, _ = business
    order_id = new_order(service, "food", "TEA")
    pay(service, order_id)
    one = propose(service, kind="refund", order_id=order_id)
    two = propose(service, kind="cancel_order", order_id=order_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda row: confirm(service, row), [one, two]))
    assert sorted(row["status"] for row in values) == ["invalidated", "submitted"]
    with Session(service.engine) as session:
        assert (
            len(
                session.scalars(
                    select(Receipt).where(
                        Receipt.action_id.in_([one["action_id"], two["action_id"]])
                    )
                ).all()
            )
            == 1
        )


def test_expiry_and_order_policy_product_snapshot_drift(business):
    service, _ = business
    order_id = new_order(service, "food", "TEA")
    pay(service, order_id)
    draft = propose(service, kind="refund", order_id=order_id)
    original_clock = service.clock
    service.clock = lambda: datetime.fromisoformat(draft["expires_at"]) + timedelta(seconds=1)
    assert confirm(service, draft)["status"] == "expired"
    service.clock = original_clock
    draft = propose(service, kind="refund", order_id=order_id)
    simulate(service, order_id, "advance")
    assert confirm(service, draft)["status"] == "invalidated"
    draft = propose(service, kind="refund", order_id=order_id)
    service.policy = Policy(version="new-demo-policy")
    assert confirm(service, draft)["status"] == "invalidated"
    draft = propose(
        service,
        kind="place_order",
        order_type="food",
        items=[{"product_id": "FOOD-A-TEA", "quantity": 1}],
        note="",
    )
    with Session(service.engine) as session, session.begin():
        product = session.get(Product, "FOOD-A-TEA")
        product.price = Decimal("20.00")
        product.version += 1
    assert confirm(service, draft)["status"] == "invalidated"


def test_snapshot_tampering_fails_closed(business):
    service, _ = business
    draft = propose(service, kind="create_ticket", message="need help", order_id=None)
    with Session(service.engine) as session, session.begin():
        row = session.get(Action, draft["action_id"])
        row.payload = {**row.payload, "message": "changed without reauthorization"}
    assert confirm(service, draft)["status"] == "invalidated"
    assert count(service, Ticket) == 0


def test_commit_failure_and_lost_ack_are_distinguished(business):
    service, _ = business
    before = count(service, Ticket)
    draft = propose(service, kind="create_ticket", order_id=None, message="private support issue")

    def before_commit(stage):
        if stage == "before_commit":
            raise RuntimeError("PRIVATE_FAILURE")

    service.fault_hook = before_commit
    with pytest.raises(BusinessError, match="business_write_failed"):
        confirm(service, draft)
    assert count(service, Ticket) == before

    def after_commit(stage):
        if stage == "after_commit":
            raise RuntimeError("PRIVATE_FAILURE")

    service.fault_hook = after_commit
    command = Confirmation(confirm=True, expected_version=1, client_request_id=key())
    with pytest.raises(UnknownOutcome) as error:
        service.confirm(A, draft["action_id"], command)
    assert error.value.references["action_id"] == draft["action_id"]
    assert count(service, Ticket) == before + 1
    service.fault_hook = None
    assert service.request_outcome(A, command.client_request_id)["committed"]
    assert service.confirm(A, draft["action_id"], command)["status"] == "submitted"
    assert count(service, Ticket) == before + 1
    with pytest.raises(BusinessError, match="request_outcome_not_recorded"):
        service.request_outcome(B, command.client_request_id)


def test_ticket_confirm_assign_reply_transition_and_privacy(business):
    service, _ = business
    draft = propose(
        service, kind="create_ticket", order_id=None, message="请处理，邮箱 user@example.com"
    )
    assert count(service, Ticket) == 0
    result = confirm(service, draft)
    ticket_id = result["result"]["ticket_id"]
    assert "example.com" not in service.messages(A, ticket_id)[0]["message"]
    for actor in [A2, B, OPB]:
        with pytest.raises(BusinessError, match="object_not_found"):
            service.messages(actor, ticket_id)
    with pytest.raises(BusinessError, match="operator_required"):
        propose(service, kind="assign_ticket", ticket_id=ticket_id, assignee_id="operator-a")
    action = propose(
        service, OP, kind="assign_ticket", ticket_id=ticket_id, assignee_id="operator-a"
    )
    confirm(service, action, OP)
    action = propose(
        service, OP, kind="reply_ticket", ticket_id=ticket_id, message="已接管，请补充信息"
    )
    confirm(service, action, OP)
    assert len(service.messages(A, ticket_id)) == 2
    action = propose(service, OP, kind="transition_ticket", ticket_id=ticket_id, status="resolved")
    confirm(service, action, OP)
    action = propose(service, OP, kind="transition_ticket", ticket_id=ticket_id, status="closed")
    confirm(service, action, OP)
    with pytest.raises(BusinessError, match="ticket_closed"):
        propose(service, kind="reply_ticket", ticket_id=ticket_id, message="late")
    with Session(service.engine) as session:
        for event in session.scalars(select(Audit)):
            assert not any(
                secret in str(event.__dict__)
                for secret in ["example.com", "请补充信息", "private support issue"]
            )


@pytest.mark.parametrize("kind", ["refund", "cancel_order"])
def test_high_approval_becomes_invalid_after_order_changes(business, kind):
    service, _ = business
    order_id = new_order(service, "ecommerce", "PHONE")
    pay(service, order_id)
    row = propose(service, kind=kind, order_id=order_id)
    row = service.review(
        OP,
        row["action_id"],
        Approval(client_request_id=key(), decision="approve", expected_version=1),
    )
    simulate(service, order_id, "advance")
    assert confirm(service, row)["status"] == "invalidated"
    assert service.order(A, order_id)["refund_status"] == "none"


def test_paid_cancel_transaction_rolls_back_both_effects(business):
    service, _ = business
    order_id = new_order(service, "food", "TEA")
    pay(service, order_id)
    row = propose(service, kind="cancel_order", order_id=order_id)

    def fail(stage):
        if stage == "before_commit":
            raise RuntimeError("stop")

    service.fault_hook = fail
    with pytest.raises(BusinessError, match="business_write_failed"):
        confirm(service, row)
    service.fault_hook = None
    order = service.order(A, order_id)
    assert order["status"] == "paid" and order["refund_status"] == "none"
    assert service.action(A, row["action_id"])["status"] == "pending_confirmation"
    with Session(service.engine) as session:
        assert session.get(Receipt, row["action_id"]) is None
