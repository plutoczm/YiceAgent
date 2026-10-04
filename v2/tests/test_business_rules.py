"""Meaningful deterministic policy and untrusted-input gates; no model calls."""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from workflowops.business.contracts import DRAFT, Confirmation
from workflowops.business.rules import BusinessError, Policy, clean_text, validate_order_action
from workflowops.identity import Sessions


@pytest.mark.parametrize(
    "kind,amount,expected",
    [
        ("ecommerce", "500.00", False),
        ("ecommerce", "500.01", True),
        ("food", "100.00", False),
        ("food", "100.01", True),
    ],
)
def test_review_boundary_is_exact(kind, amount, expected):
    assert Policy().approval(kind, Decimal(amount)) is expected


@pytest.mark.parametrize(
    "fields",
    [
        {"tenant_id": "tenant-b"},
        {"customer_id": "customer-b1"},
        {"amount": "0.01"},
        {"approved": True},
        {"database_url": "postgresql://foreign"},
        {"role": "operator"},
    ],
)
def test_draft_refuses_authority_and_price_fields(fields):
    data = {"kind": "refund", "order_id": "ECO-A-01", "client_request_id": str(uuid4()), **fields}
    with pytest.raises(ValidationError):
        DRAFT.validate_python(data)


@pytest.mark.parametrize("quantity", [True, 0, 21, "2", 1.5])
def test_cart_quantity_is_a_bounded_strict_integer(quantity):
    with pytest.raises(ValidationError):
        DRAFT.validate_python(
            {
                "kind": "place_order",
                "order_type": "ecommerce",
                "items": [{"product_id": "ECO-A-CABLE", "quantity": quantity}],
                "client_request_id": str(uuid4()),
            }
        )


def test_confirm_refuses_bool_version_and_truthy_string():
    for fields in [
        {"confirm": "yes", "expected_version": 1},
        {"confirm": True, "expected_version": True},
    ]:
        with pytest.raises(ValidationError):
            Confirmation(client_request_id=str(uuid4()), **fields)


@pytest.mark.parametrize(
    "family,status,allowed",
    [
        ("ecommerce", "ready_to_ship", True),
        ("ecommerce", "shipped", False),
        ("food", "paid", True),
        ("food", "accepted", False),
    ],
)
def test_cancellation_boundary(family, status, allowed):
    row = SimpleNamespace(
        order_type=family,
        status=status,
        payment_status="paid",
        refund_status="none",
        paid=Decimal("19.90"),
        refunded=Decimal("0"),
    )
    if allowed:
        validate_order_action("cancel_order", row)
    else:
        with pytest.raises(BusinessError, match="cancellation_not_available"):
            validate_order_action("cancel_order", row)


def test_guardrails_redact_and_reject_credentials():
    assert "example.com" not in clean_text("contact person@example.com or 13800138000")
    with pytest.raises(BusinessError, match="credential_in_message"):
        clean_text("Bearer SUPER_SECRET_CREDENTIAL")


def test_switch_revokes_old_session_and_changes_csrf():
    sessions = Sessions()
    old, a = sessions.create("customer-a1")
    new, b = sessions.create("operator-a", old)
    assert new != old and a.csrf != b.csrf
    with pytest.raises(PermissionError):
        sessions.get(old)
    assert sessions.get(new).principal.operator


def test_money_does_not_render_nan_or_silently_round_more_than_cents():
    from workflowops.business.rules import money

    for value in [Decimal("NaN"), Decimal("Infinity"), Decimal("1.001")]:
        with pytest.raises(BusinessError, match="invalid_business_amount"):
            money(value)
