"""Deterministic demo policies. These are not Taobao or food-platform official rules."""

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

PATHS = {
    "ecommerce": ("awaiting_payment", "paid", "ready_to_ship", "shipped", "delivered"),
    "food": ("awaiting_payment", "paid", "accepted", "preparing", "delivering", "delivered"),
}
TICKET_TRANSITIONS = {
    "open": {"assigned", "pending_customer", "resolved"},
    "assigned": {"pending_customer", "resolved"},
    "pending_customer": {"assigned", "resolved"},
    "resolved": {"assigned", "closed"},
    "closed": set(),
}


class BusinessError(ValueError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Policy:
    version: str = "demo-business/v2.1"
    ecommerce_threshold: Decimal = Decimal("500.00")
    food_threshold: Decimal = Decimal("100.00")

    def threshold(self, order_type: str) -> Decimal:
        return self.ecommerce_threshold if order_type == "ecommerce" else self.food_threshold

    def approval(self, order_type: str, amount: Decimal) -> bool:
        return amount > self.threshold(order_type)


def money(value: Decimal) -> str:
    if not value.is_finite() or value != value.quantize(Decimal("0.01")):
        raise BusinessError("invalid_business_amount", 503)
    return format(value.quantize(Decimal("0.01")), "f")


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def clean_text(value: str) -> str:
    if re.search(r"\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9_.-]{16,})", value):
        raise BusinessError("credential_in_message", 422)
    value = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[REDACTED_EMAIL]", value, flags=re.I
    )
    return re.sub(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "[REDACTED_PHONE]", value)


def validate_order_action(kind: str, order: Any) -> None:
    if kind == "pay":
        if order.status != "awaiting_payment" or order.payment_status not in {"unpaid", "failed"}:
            raise BusinessError("payment_not_available")
    elif kind == "cancel_order":
        allowed = (
            {"awaiting_payment", "paid", "ready_to_ship"}
            if order.order_type == "ecommerce"
            else {"awaiting_payment", "paid"}
        )
        if order.status not in allowed:
            raise BusinessError("cancellation_not_available")
        if order.payment_status == "pending":
            raise BusinessError("payment_in_progress")
        if order.refund_status in {"requested", "refunded"}:
            raise BusinessError("refund_already_recorded")
    elif kind == "refund":
        if order.payment_status != "paid" or order.paid <= 0:
            raise BusinessError("unpaid_order_not_refundable")
        if order.refund_status in {"requested", "refunded"} or order.refunded > 0:
            raise BusinessError("refund_already_recorded")
