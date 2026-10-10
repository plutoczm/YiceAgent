"""Same-origin demo API: server-derived identity, CSRF and closed human command DTOs."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from yice_agent.business.contracts import (
    Approval,
    Confirmation,
    Draft,
    PersonaSelection,
    SimulationEvent,
)
from yice_agent.business.rules import money
from yice_agent.business.service import BusinessService
from yice_agent.identity import Principal, Sessions

COOKIE = "yice_agent_session"


def router(service: BusinessService, sessions: Sessions) -> APIRouter:
    api = APIRouter(prefix="/api/v2")

    def current(request: Request) -> Principal:
        try:
            session = sessions.get(request.cookies.get(COOKIE))
        except PermissionError:
            raise HTTPException(401, "demo_session_required") from None
        if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
            request.headers.get("X-Yice-Agent-CSRF", ""), session.csrf
        ):
            raise HTTPException(403, "csrf_required")
        return session.principal

    Actor = Annotated[Principal, Depends(current)]

    @api.post("/demo/session")
    def select_persona(body: PersonaSelection, request: Request):
        token, session = sessions.create(body.persona, request.cookies.get(COOKIE))
        from fastapi.responses import JSONResponse

        response = JSONResponse(
            {
                "persona": session.principal.subject,
                "operator": session.principal.operator,
                "csrf_token": session.csrf,
                "mode": "local-demo",
            }
        )
        response.set_cookie(
            COOKIE, token, httponly=True, samesite="strict", secure=False, path="/", max_age=7200
        )
        return response

    @api.get("/demo/session")
    def get_session(request: Request):
        try:
            session = sessions.get(request.cookies.get(COOKIE))
        except PermissionError:
            raise HTTPException(401, "demo_session_required") from None
        return {
            "persona": session.principal.subject,
            "operator": session.principal.operator,
            "csrf_token": session.csrf,
            "mode": "local-demo",
        }

    @api.get("/catalog")
    def products(actor: Actor):
        return service.products(actor)

    @api.get("/orders")
    def orders(actor: Actor):
        return service.orders(actor)

    @api.get("/orders/{order_id}")
    def order(order_id: str, actor: Actor):
        return service.order(actor, order_id)

    @api.post("/orders/drafts")
    def order_draft(body: Draft, actor: Actor):
        if body.kind != "place_order":
            raise HTTPException(422, "place_order_required")
        return service.propose(actor, body)

    @api.get("/actions")
    def actions(actor: Actor):
        return service.actions(actor)

    @api.get("/actions/requests/{request_id}")
    def request_outcome(request_id: str, actor: Actor):
        return service.request_outcome(actor, request_id)

    @api.get("/actions/{action_id}")
    def action(action_id: str, actor: Actor):
        return service.action(actor, action_id)

    @api.post("/actions")
    def propose(body: Draft, actor: Actor):
        return service.propose(actor, body)

    @api.post("/actions/{action_id}/confirm")
    def confirm(action_id: str, body: Confirmation, actor: Actor):
        return service.confirm(actor, action_id, body)

    @api.post("/actions/{action_id}/review")
    def review(action_id: str, body: Approval, actor: Actor):
        return service.review(actor, action_id, body)

    @api.post("/simulator/events")
    def simulate(body: SimulationEvent, actor: Actor):
        return service.simulate(actor, body)

    @api.get("/tickets")
    def tickets(actor: Actor):
        return service.tickets(actor)

    @api.get("/tickets/{ticket_id}/messages")
    def messages(ticket_id: str, actor: Actor):
        return service.messages(actor, ticket_id)

    @api.get("/policies")
    def policies(actor: Actor):
        return {
            "version": service.policy.version,
            "source": "demo-rules",
            "ecommerce_refund_review_above": money(service.policy.ecommerce_threshold),
            "food_refund_review_above": money(service.policy.food_threshold),
            "draft_ttl_seconds": 900,
            "rules": [
                "电商发货前可取消，外卖接单前可取消。",
                "支付、退款结果由运营模拟事件推进。",
                "高金额退款先运营审批，再客户确认。",
                "普通退款不自动撤销配送；支付后的取消合并申请整单退款。",
            ],
        }

    return api
