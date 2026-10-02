from app.domain import Intent
from app.router import IntentRouter


def test_shipping_address_how_can_question_routes_to_knowledge() -> None:
    decision = IntentRouter().route(
        "how can I change the shipping address before fulfillment locks the order?"
    )
    assert decision.intent is Intent.KNOWLEDGE
    assert decision.reason == "shipping/order policy question without order"


def test_shipping_policy_how_do_question_routes_to_knowledge() -> None:
    decision = IntentRouter().route("how do shipping address changes work before dispatch?")
    assert decision.intent is Intent.KNOWLEDGE


def test_tracking_request_without_policy_marker_still_routes_to_order_status() -> None:
    decision = IntentRouter().route("I need tracking for my order")
    assert decision.intent is Intent.ORDER_STATUS
