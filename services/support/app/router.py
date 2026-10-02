from __future__ import annotations

import re

from app.domain import Intent, RoutingDecision
from app.model_gateway import ModelGatewayError, OpenAICompatibleModel


class IntentRouter:
    """LLM-assisted intent classification with a deterministic fallback."""

    def __init__(self, model: OpenAICompatibleModel | None = None) -> None:
        self.model = model

    def route(self, text: str) -> RoutingDecision:
        if self.model is not None:
            decision = self._route_with_model(text)
            if decision is not None:
                return decision
        return self._route_with_rules(text)

    def _route_with_model(self, text: str) -> RoutingDecision | None:
        system = (
            "Classify one support message. Return JSON only with keys intent, confidence, reason. "
            "Allowed intent values: knowledge, order_status, refund, return_request, complaint, "
            "unknown. Never choose tools and never claim that an action was executed."
        )
        try:
            data = self.model.chat_json(system=system, user=text)
            return RoutingDecision(
                intent=Intent(str(data["intent"])),
                confidence=float(data.get("confidence", 0.7)),
                reason=str(data.get("reason", "model classification")),
                source="llm",
            )
        except (ModelGatewayError, KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _route_with_rules(text: str) -> RoutingDecision:
        normalized = text.lower()
        has_order_id = bool(re.search(r"\bord-\d{4,}\b", normalized))
        question_markers = (
            "政策",
            "规则",
            "条件",
            "能否",
            "可以吗",
            "how",
            "policy",
            "eligible",
        )
        explicit_policy_markers = (
            "政策",
            "规则",
            "条件",
            "policy",
            "how does",
            "eligible",
        )

        if any(word in normalized for word in ("投诉", "complaint", "人工客服", "人工处理")):
            return RoutingDecision(
                intent=Intent.COMPLAINT,
                confidence=0.96,
                reason="complaint keyword",
            )
        if any(word in normalized for word in ("退款", "refund", "退钱")):
            if not has_order_id and any(word in normalized for word in question_markers):
                return RoutingDecision(
                    intent=Intent.KNOWLEDGE,
                    confidence=0.9,
                    reason="refund policy question without order",
                )
            return RoutingDecision(intent=Intent.REFUND, confidence=0.95, reason="refund keyword")
        if any(word in normalized for word in ("退货", "return item", "return this")):
            if not has_order_id and any(word in normalized for word in question_markers):
                return RoutingDecision(
                    intent=Intent.KNOWLEDGE,
                    confidence=0.9,
                    reason="return policy question without order",
                )
            return RoutingDecision(
                intent=Intent.RETURN_REQUEST,
                confidence=0.95,
                reason="return keyword",
            )

        order_status_terms = ("订单状态", "物流", "到哪", "order status", "shipping", "tracking")
        address_terms = ("收货地址", "配送地址", "shipping address", "delivery address")
        address_change_terms = ("修改", "更改", "改址", "change", "update", "edit")
        if (
            not has_order_id
            and any(word in normalized for word in address_terms)
            and any(word in normalized for word in address_change_terms)
        ):
            return RoutingDecision(
                intent=Intent.KNOWLEDGE,
                confidence=0.92,
                reason="shipping/order policy question without order",
            )
        if (
            not has_order_id
            and any(word in normalized for word in order_status_terms)
            and any(word in normalized for word in explicit_policy_markers)
        ):
            return RoutingDecision(
                intent=Intent.KNOWLEDGE,
                confidence=0.9,
                reason="shipping/order policy question without order",
            )
        if any(word in normalized for word in order_status_terms):
            return RoutingDecision(
                intent=Intent.ORDER_STATUS,
                confidence=0.93,
                reason="order-status keyword",
            )
        if any(
            word in normalized
            for word in ("政策", "规则", "怎么", "如何", "能否", "coupon", "policy", "how")
        ):
            return RoutingDecision(
                intent=Intent.KNOWLEDGE,
                confidence=0.82,
                reason="knowledge keyword",
            )
        return RoutingDecision(intent=Intent.UNKNOWN, confidence=0.5, reason="no known intent")
