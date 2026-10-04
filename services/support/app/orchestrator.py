from __future__ import annotations

import re
from uuid import uuid4

from app.auth import Principal
from app.domain import (
    ConfirmationResponse,
    Intent,
    OperationStatus,
    PendingActionStatus,
    SupportRequest,
    SupportResponse,
    TicketPriority,
)
from app.guardrails import InputGuardrails
from app.knowledge import KnowledgeService
from app.router import IntentRouter
from app.store import SupportStore
from app.tools import SupportTools
from app.workflows import (
    ConfirmActionRequest,
    CustomerContext,
    RefundPreparationRequest,
    ReturnPreparationRequest,
)


class SupportOrchestrator:
    def __init__(
        self,
        *,
        store: SupportStore,
        guardrails: InputGuardrails,
        router: IntentRouter,
        knowledge: KnowledgeService,
        tools: SupportTools,
    ) -> None:
        self.store = store
        self.guardrails = guardrails
        self.router = router
        self.knowledge = knowledge
        self.tools = tools

    def handle(self, request: SupportRequest, principal: Principal) -> SupportResponse:
        trace_id = self._trace_id()
        customer_id = principal.require_customer()
        tenant_id = principal.tenant_id
        safety = self.guardrails.inspect(request.message)
        if safety.blocked:
            self.store.record_audit(
                trace_id=trace_id,
                actor_id=principal.subject,
                operation="guardrail.inspect",
                resource_type="conversation",
                resource_id=request.conversation_id,
                outcome="blocked",
                details={"labels": safety.labels},
                tenant_id=tenant_id,
            )
            return SupportResponse(
                trace_id=trace_id,
                intent=Intent.UNKNOWN,
                answer="该请求包含试图绕过系统安全边界的指令，未调用模型或业务工具。",
                safety_labels=safety.labels,
            )

        safe_message = safety.sanitized_text
        route = self.router.route(safe_message)
        self.store.record_audit(
            trace_id=trace_id,
            actor_id=principal.subject,
            operation="agent.route",
            resource_type="conversation",
            resource_id=request.conversation_id,
            outcome="success",
            details={
                "intent": route.intent.value,
                "confidence": route.confidence,
                "source": route.source,
            },
            tenant_id=tenant_id,
        )

        if route.intent is Intent.KNOWLEDGE:
            search = self.knowledge.search_with_status(safe_message)
            citations = search.citations
            self.store.record_audit(
                trace_id=trace_id,
                actor_id=principal.subject,
                operation="knowledge.search",
                resource_type="knowledge",
                resource_id=None,
                outcome="degraded" if search.degraded else "success",
                details={
                    "citation_count": len(citations),
                    "degraded": search.degraded,
                    "degradation_reason": search.degradation_reason,
                    "chunk_ids": [citation.chunk_id for citation in citations],
                    "document_versions": [citation.document_version for citation in citations],
                },
                tenant_id=tenant_id,
            )
            return SupportResponse(
                trace_id=trace_id,
                intent=route.intent,
                answer=self.knowledge.answer(safe_message, citations),
                citations=citations,
                safety_labels=safety.labels,
                retrieval_degraded=search.degraded,
                retrieval_degradation_reason=search.degradation_reason,
            )
        if route.intent is Intent.ORDER_STATUS:
            return self._order_status(
                customer_id=customer_id,
                actor_id=principal.subject,
                message=safe_message,
                intent=route.intent,
                trace_id=trace_id,
                safety_labels=safety.labels,
                tenant_id=tenant_id,
            )
        if route.intent is Intent.REFUND:
            order_id = self._extract_order_id(safe_message)
            if order_id is None:
                return self._missing_order_id(route.intent, trace_id, safety.labels)
            return self.prepare_refund(
                customer_id=customer_id,
                actor_id=principal.subject,
                conversation_id=request.conversation_id,
                order_id=order_id,
                trace_id=trace_id,
                safety_labels=safety.labels,
                tenant_id=tenant_id,
            )
        if route.intent is Intent.RETURN_REQUEST:
            return self._prepare_return(
                customer_id=customer_id,
                actor_id=principal.subject,
                conversation_id=request.conversation_id,
                message=safe_message,
                trace_id=trace_id,
                safety_labels=safety.labels,
                tenant_id=tenant_id,
            )
        if route.intent is Intent.COMPLAINT:
            ticket = self.tools.create_ticket(
                customer_id=customer_id,
                conversation_id=request.conversation_id,
                reason=safe_message,
                trace_id=trace_id,
                actor_id=principal.subject,
                priority=TicketPriority.NORMAL,
                tenant_id=tenant_id,
            )
            return SupportResponse(
                trace_id=trace_id,
                intent=route.intent,
                answer="已创建客服工单并转人工处理。",
                ticket_id=ticket.ticket_id,
                handoff=True,
                safety_labels=safety.labels,
            )

        ticket = self.tools.create_ticket(
            customer_id=customer_id,
            conversation_id=request.conversation_id,
            reason=f"unresolved_request: {safe_message}",
            trace_id=trace_id,
            actor_id=principal.subject,
            priority=TicketPriority.LOW,
            tenant_id=tenant_id,
        )
        return SupportResponse(
            trace_id=trace_id,
            intent=Intent.UNKNOWN,
            answer="当前自动化流程无法可靠处理该请求，已转人工客服。",
            ticket_id=ticket.ticket_id,
            handoff=True,
            safety_labels=safety.labels,
        )

    def prepare_refund(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        actor_id: str,
        conversation_id: str,
        order_id: str,
        trace_id: str | None = None,
        safety_labels: list[str] | None = None,
    ) -> SupportResponse:
        trace_id = trace_id or self._trace_id()
        labels = safety_labels or []
        prepared = self.tools.prepare_refund(
            request=RefundPreparationRequest(order_id, conversation_id),
            context=CustomerContext(customer_id, actor_id, trace_id, tenant_id=tenant_id),
        )
        if not prepared.eligibility.eligible:
            exc = prepared.eligibility.reason_code
            return SupportResponse(
                trace_id=trace_id,
                intent=Intent.REFUND,
                answer=f"无法创建退款请求：{exc}。",
                safety_labels=labels,
            )

        operation = prepared.operation
        if operation is not None and operation.status is OperationStatus.SUBMITTED:
            return SupportResponse(
                trace_id=trace_id,
                intent=Intent.REFUND,
                answer="\u8be5\u8ba2\u5355\u7684\u9000\u6b3e\u7533\u8bf7\u5df2\u5728\u672c\u5730\u8bb0\u5f55\u3002",
                operation_id=operation.operation_id,
                operation_status=operation.status.value,
                safety_labels=labels,
            )
        amount = prepared.eligibility.quoted_amount
        assert amount is not None
        if prepared.ticket is not None and operation is not None and not operation.human_approved:
            ticket = prepared.ticket
            return SupportResponse(
                trace_id=trace_id,
                intent=Intent.REFUND,
                answer=f"该订单预计可退 {amount:.2f}，金额超过自动处理阈值，已转人工审核。",
                ticket_id=ticket.ticket_id,
                operation_id=operation.operation_id,
                operation_status=operation.status.value,
                handoff=True,
                safety_labels=labels,
            )

        pending = prepared.pending
        assert pending is not None
        return SupportResponse(
            trace_id=trace_id,
            intent=Intent.REFUND,
            answer=(
                f"订单 {order_id} 预计可退 {amount:.2f}。需要你在有效期内明确确认后才会提交退款。"
            ),
            pending_action_id=pending.action_id,
            operation_id=operation.operation_id,
            operation_status=operation.status.value,
            pending_action_expires_at=pending.expires_at,
            safety_labels=labels,
        )

    def resolve_action(
        self,
        action_id: str,
        principal: Principal,
        *,
        confirm: bool,
    ) -> ConfirmationResponse:
        trace_id = self._trace_id()
        operation = self.tools.resolve_action(
            request=ConfirmActionRequest(action_id, confirm),
            context=CustomerContext(
                principal.require_customer(),
                principal.subject,
                trace_id,
                principal.tenant_id,
            ),
        )
        status = {"pending_confirmation": "pending", "submitted": "executed"}.get(
            operation.status.value, operation.status.value
        )
        return ConfirmationResponse(
            trace_id=trace_id,
            action_id=action_id,
            status=PendingActionStatus(status),
            operation_status=operation.status.value,
            result=operation.result,
        )

    def _order_status(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        actor_id: str,
        message: str,
        intent: Intent,
        trace_id: str,
        safety_labels: list[str],
    ) -> SupportResponse:
        order_id = self._extract_order_id(message)
        if order_id is None:
            return self._missing_order_id(intent, trace_id, safety_labels)
        order = self.tools.get_order(
            order_id=order_id,
            customer_id=customer_id,
            trace_id=trace_id,
            actor_id=actor_id,
            tenant_id=tenant_id,
        )
        if order is None:
            return SupportResponse(
                trace_id=trace_id,
                intent=intent,
                answer="未找到属于当前客户的订单，未暴露其他客户的订单信息。",
                safety_labels=safety_labels,
            )
        return SupportResponse(
            trace_id=trace_id,
            intent=intent,
            answer=f"订单 {order.order_id} 当前状态：{order.status}。",
            safety_labels=safety_labels,
        )

    def _prepare_return(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        actor_id: str,
        conversation_id: str,
        message: str,
        trace_id: str,
        safety_labels: list[str],
    ) -> SupportResponse:
        order_id = self._extract_order_id(message)
        if order_id is None:
            return self._missing_order_id(Intent.RETURN_REQUEST, trace_id, safety_labels)
        prepared = self.tools.prepare_return(
            request=ReturnPreparationRequest(order_id, conversation_id),
            context=CustomerContext(customer_id, actor_id, trace_id, tenant_id=tenant_id),
        )
        if prepared.ticket is not None:
            ticket = prepared.ticket
            return SupportResponse(
                trace_id=trace_id,
                intent=Intent.RETURN_REQUEST,
                answer="该订单当前不能自动创建退货申请，已转人工客服核验。",
                ticket_id=ticket.ticket_id,
                handoff=True,
                safety_labels=safety_labels,
            )
        operation = prepared.operation
        if operation is not None and operation.status is OperationStatus.SUBMITTED:
            return SupportResponse(
                trace_id=trace_id,
                intent=Intent.RETURN_REQUEST,
                answer="\u8be5\u8ba2\u5355\u7684\u9000\u8d27\u7533\u8bf7\u5df2\u5728\u672c\u5730\u8bb0\u5f55\u3002",
                operation_id=operation.operation_id,
                operation_status=operation.status.value,
                safety_labels=safety_labels,
            )
        pending = prepared.pending
        assert pending is not None
        return SupportResponse(
            trace_id=trace_id,
            intent=Intent.RETURN_REQUEST,
            answer=f"订单 {order_id} 可以申请退货，需要你在有效期内明确确认后才会提交。",
            pending_action_id=pending.action_id,
            operation_id=operation.operation_id,
            operation_status=operation.status.value,
            pending_action_expires_at=pending.expires_at,
            safety_labels=safety_labels,
        )

    @staticmethod
    def _extract_order_id(text: str) -> str | None:
        match = re.search(r"\bORD-\d{4,}\b", text.upper())
        return match.group(0) if match else None

    @staticmethod
    def _missing_order_id(
        intent: Intent,
        trace_id: str,
        safety_labels: list[str],
    ) -> SupportResponse:
        return SupportResponse(
            trace_id=trace_id,
            intent=intent,
            answer="请提供订单号（例如 ORD-1001），系统不会猜测或查询其他客户订单。",
            safety_labels=safety_labels,
        )

    @staticmethod
    def _trace_id() -> str:
        return f"TRC-{uuid4().hex[:16]}"
