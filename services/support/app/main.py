from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.auth import AuthenticationError, AuthorizationError, Principal
from app.container import ServiceContainer, build_container
from app.domain import (
    ApprovalRequest,
    AuditEventView,
    ConfirmationRequest,
    ConfirmationResponse,
    OperationView,
    OrderView,
    SupportRequest,
    SupportResponse,
    TicketAssignRequest,
    TicketMessageRequest,
    TicketMessageRole,
    TicketMessageView,
    TicketStatus,
    TicketTransitionRequest,
    TicketView,
)
from app.operations import OperationConflict
from app.reliability import MutationLockBusy, ReliabilityBackendError

STATIC_DIR = Path(__file__).resolve().parent / "static"


def get_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_principal_id: Annotated[str | None, Header()] = None,
    x_customer_id: Annotated[str | None, Header()] = None,
    x_roles: Annotated[str | None, Header()] = None,
) -> Principal:
    services: ServiceContainer = request.app.state.services
    try:
        return services.auth.authenticate(
            authorization=authorization,
            dev_principal_id=x_principal_id,
            dev_customer_id=x_customer_id,
            dev_roles=x_roles,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def get_customer_principal(
    current: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    try:
        current.require_customer()
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return current


def get_agent_principal(
    current: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    try:
        current.require_agent()
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return current


def _enforce_rate_limit(
    services: ServiceContainer,
    *,
    current: Principal,
    scope: str,
    limit: int,
) -> None:
    try:
        decision = services.reliability.check_rate_limit(
            scope=scope,
            subject=f"{current.tenant_id}:{current.subject}",
            limit=limit,
            window_seconds=services.settings.rate_limit_window_seconds,
        )
    except ReliabilityBackendError as exc:
        if services.settings.rate_limit_fail_open:
            return
        raise HTTPException(status_code=503, detail=exc.reason) from exc
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="rate_limit_exceeded",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )


def create_app(container: ServiceContainer | None = None) -> FastAPI:
    services = container or build_container()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            services.observability.shutdown()

    app = FastAPI(
        title="SupportOps AI",
        version="0.7.0",
        description=(
            "AI customer operations with guarded tools, identity, audit, distributed "
            "reliability, observability and human handoff."
        ),
        lifespan=lifespan,
    )
    app.state.services = services

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/ui/")

    @app.middleware("http")
    async def observe_request(request: Request, call_next):
        started = perf_counter()
        status_code = 500
        route = "unmatched"
        parent_context = services.observability.extract_context(request.headers)
        try:
            with services.observability.operation(
                "http.server.request",
                component="http",
                backend="fastapi",
                context=parent_context,
            ) as observation:
                observation.set_attribute("http.request.method", request.method)
                response = await call_next(request)
                status_code = response.status_code
                route_object = request.scope.get("route")
                route = str(getattr(route_object, "path", "unmatched"))
                observation.set_attribute("http.route", route)
                observation.set_attribute("http.response.status_code", status_code)
                if status_code >= 500:
                    observation.set_outcome("server_error")
                elif status_code >= 400:
                    observation.set_outcome("client_error")
                return response
        finally:
            route_object = request.scope.get("route")
            route = str(getattr(route_object, "path", route))
            services.observability.record_http(
                method=request.method,
                route=route,
                status_code=status_code,
                duration_seconds=perf_counter() - started,
            )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "reliability_backend": services.reliability.backend_name,
            "metrics": "enabled" if services.settings.metrics_enabled else "disabled",
            "tracing": "enabled" if services.settings.tracing_enabled else "disabled",
        }

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        if not services.settings.metrics_enabled:
            raise HTTPException(status_code=404, detail="metrics_disabled")
        return Response(
            content=services.observability.render_metrics(),
            media_type=services.observability.metrics_content_type,
        )

    @app.post("/v1/support/messages", response_model=SupportResponse)
    def handle_support_message(
        request: SupportRequest,
        current: Annotated[Principal, Depends(get_customer_principal)],
    ) -> SupportResponse:
        _enforce_rate_limit(
            services,
            current=current,
            scope="support-message",
            limit=services.settings.support_message_rate_limit,
        )
        response = services.orchestrator.handle(request, current)
        if response.ticket_id:
            services.store.create_ticket_message(
                ticket_id=response.ticket_id,
                sender_role=TicketMessageRole.CUSTOMER,
                sender_id=current.subject,
                body=services.orchestrator.guardrails.inspect(request.message).sanitized_text,
                tenant_id=current.tenant_id,
                customer_id=current.require_customer(),
            )
            services.store.create_ticket_message(
                ticket_id=response.ticket_id,
                sender_role=TicketMessageRole.ASSISTANT,
                sender_id="supportops-ai",
                body=response.answer,
                context=response.model_dump(mode="json"),
                tenant_id=current.tenant_id,
                customer_id=current.require_customer(),
            )
        return response

    @app.get("/v1/tickets/{ticket_id}/messages", response_model=list[TicketMessageView])
    def list_customer_ticket_messages(
        ticket_id: str,
        current: Annotated[Principal, Depends(get_customer_principal)],
    ) -> list[TicketMessageView]:
        ticket = services.store.get_ticket(
            ticket_id, tenant_id=current.tenant_id, customer_id=current.require_customer()
        )
        if ticket is None or ticket.customer_id != current.require_customer():
            raise HTTPException(status_code=404, detail="ticket_not_found")
        return services.store.list_ticket_messages(
            ticket_id, tenant_id=current.tenant_id, customer_id=current.require_customer()
        )

    @app.get("/v1/conversations/{conversation_id}/ticket", response_model=TicketView)
    def get_customer_conversation_ticket(
        conversation_id: str,
        current: Annotated[Principal, Depends(get_customer_principal)],
    ) -> TicketView:
        ticket = services.store.find_latest_ticket_for_conversation(
            conversation_id=conversation_id,
            customer_id=current.require_customer(),
            tenant_id=current.tenant_id,
        )
        if ticket is None:
            raise HTTPException(status_code=404, detail="ticket_not_found")
        return ticket

    @app.post(
        "/v1/tickets/{ticket_id}/messages",
        response_model=TicketMessageView,
        status_code=201,
    )
    def send_customer_ticket_message(
        ticket_id: str,
        request: TicketMessageRequest,
        current: Annotated[Principal, Depends(get_customer_principal)],
    ) -> TicketMessageView:
        ticket = services.store.get_ticket(
            ticket_id, tenant_id=current.tenant_id, customer_id=current.require_customer()
        )
        if ticket is None or ticket.customer_id != current.require_customer():
            raise HTTPException(status_code=404, detail="ticket_not_found")
        if ticket.status in {TicketStatus.CLOSED, TicketStatus.RESOLVED}:
            raise HTTPException(status_code=409, detail="ticket_not_open_for_messages")
        message = services.store.create_ticket_message(
            ticket_id=ticket_id,
            sender_role=TicketMessageRole.CUSTOMER,
            sender_id=current.subject,
            body=request.message,
            tenant_id=current.tenant_id,
            customer_id=current.require_customer(),
        )
        if ticket.status is TicketStatus.PENDING_CUSTOMER:
            services.tickets.transition(
                ticket_id=ticket_id,
                target=TicketStatus.ASSIGNED,
                actor_id=current.subject,
                trace_id=f"TRC-{uuid4().hex[:16]}",
                note="customer_replied",
                tenant_id=current.tenant_id,
            )
        return message

    @app.post("/v1/actions/{action_id}/confirm", response_model=ConfirmationResponse)
    def confirm_action(
        action_id: str,
        request: ConfirmationRequest,
        current: Annotated[Principal, Depends(get_customer_principal)],
    ) -> ConfirmationResponse:
        _enforce_rate_limit(
            services,
            current=current,
            scope="action-confirmation",
            limit=services.settings.action_confirmation_rate_limit,
        )
        try:
            return services.orchestrator.resolve_action(
                action_id,
                current,
                confirm=request.confirm,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except OperationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except MutationLockBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ReliabilityBackendError as exc:
            raise HTTPException(status_code=503, detail=exc.reason) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/operations/{operation_id}", response_model=OperationView)
    def get_operation(
        operation_id: str,
        current: Annotated[Principal, Depends(get_principal)],
    ) -> OperationView:
        try:
            return services.operations.get(operation_id, principal=current)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except OperationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="operation_not_found") from exc

    @app.get("/v1/agent/tickets/{ticket_id}/operation", response_model=OperationView)
    def get_ticket_operation(
        ticket_id: str,
        current: Annotated[Principal, Depends(get_agent_principal)],
    ) -> OperationView:
        try:
            return services.operations.for_ticket(ticket_id, principal=current)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="operation_not_found") from exc

    @app.post("/v1/agent/operations/{operation_id}/decision", response_model=OperationView)
    def decide_operation(
        operation_id: str,
        request: ApprovalRequest,
        current: Annotated[Principal, Depends(get_principal)],
    ) -> OperationView:
        try:
            return services.operations.decide(
                operation_id,
                decision=request.decision,
                expected_version=request.expected_version,
                principal=current,
                trace_id=f"TRC-{uuid4().hex[:16]}",
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except OperationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="operation_not_found") from exc

    @app.get("/v1/orders/{order_id}", response_model=OrderView)
    def get_order(
        order_id: str,
        current: Annotated[Principal, Depends(get_customer_principal)],
    ) -> OrderView:
        customer_id = current.require_customer()
        trace_id = f"TRC-{uuid4().hex[:16]}"
        order = services.tools.get_order(
            order_id=order_id.upper(),
            customer_id=customer_id,
            trace_id=trace_id,
            actor_id=current.subject,
            tenant_id=current.tenant_id,
        )
        if order is None:
            raise HTTPException(status_code=404, detail="order_not_found")
        return order

    @app.get("/v1/tickets/{ticket_id}", response_model=TicketView)
    def get_ticket(
        ticket_id: str,
        current: Annotated[Principal, Depends(get_customer_principal)],
    ) -> TicketView:
        customer_id = current.require_customer()
        ticket = services.store.get_ticket(
            ticket_id, tenant_id=current.tenant_id, customer_id=current.require_customer()
        )
        if ticket is None or ticket.customer_id != customer_id:
            raise HTTPException(status_code=404, detail="ticket_not_found")
        return ticket

    @app.get("/v1/agent/tickets", response_model=list[TicketView])
    def list_agent_tickets(
        current: Annotated[Principal, Depends(get_agent_principal)],
        status: Annotated[TicketStatus | None, Query()] = None,
    ) -> list[TicketView]:
        return services.store.list_tickets(status, tenant_id=current.tenant_id)

    @app.post("/v1/agent/tickets/{ticket_id}/assign", response_model=TicketView)
    def assign_ticket(
        ticket_id: str,
        request: TicketAssignRequest,
        current: Annotated[Principal, Depends(get_agent_principal)],
    ) -> TicketView:
        try:
            return services.tickets.assign(
                ticket_id=ticket_id,
                assignee_id=request.assignee_id,
                actor_id=current.subject,
                trace_id=f"TRC-{uuid4().hex[:16]}",
                tenant_id=current.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ticket_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/agent/tickets/{ticket_id}/transition", response_model=TicketView)
    def transition_ticket(
        ticket_id: str,
        request: TicketTransitionRequest,
        current: Annotated[Principal, Depends(get_agent_principal)],
    ) -> TicketView:
        try:
            return services.tickets.transition(
                ticket_id=ticket_id,
                target=request.status,
                actor_id=current.subject,
                trace_id=f"TRC-{uuid4().hex[:16]}",
                note=request.note,
                tenant_id=current.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ticket_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/v1/agent/tickets/{ticket_id}/messages",
        response_model=list[TicketMessageView],
    )
    def list_agent_ticket_messages(
        ticket_id: str,
        current: Annotated[Principal, Depends(get_agent_principal)],
    ) -> list[TicketMessageView]:
        ticket = services.store.get_ticket(ticket_id, tenant_id=current.tenant_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="ticket_not_found")
        return services.store.list_ticket_messages(ticket_id, tenant_id=current.tenant_id)

    @app.post(
        "/v1/agent/tickets/{ticket_id}/messages",
        response_model=TicketMessageView,
        status_code=201,
    )
    def send_agent_ticket_message(
        ticket_id: str,
        request: TicketMessageRequest,
        current: Annotated[Principal, Depends(get_agent_principal)],
    ) -> TicketMessageView:
        ticket = services.store.get_ticket(ticket_id, tenant_id=current.tenant_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="ticket_not_found")
        if ticket.status in {TicketStatus.CLOSED, TicketStatus.RESOLVED}:
            raise HTTPException(status_code=409, detail="ticket_not_open_for_messages")
        if ticket.assignee_id is None:
            raise HTTPException(status_code=409, detail="ticket_not_assigned")
        if ticket.assignee_id != current.subject and "support_admin" not in current.roles:
            raise HTTPException(status_code=403, detail="ticket_assigned_to_another_agent")
        message = services.store.create_ticket_message(
            ticket_id=ticket_id,
            sender_role=TicketMessageRole.AGENT,
            sender_id=current.subject,
            body=request.message,
            tenant_id=current.tenant_id,
        )
        if ticket.status is TicketStatus.ASSIGNED:
            services.tickets.transition(
                ticket_id=ticket_id,
                target=TicketStatus.PENDING_CUSTOMER,
                actor_id=current.subject,
                trace_id=f"TRC-{uuid4().hex[:16]}",
                note="agent_replied",
                tenant_id=current.tenant_id,
            )
        services.store.record_audit(
            trace_id=f"TRC-{uuid4().hex[:16]}",
            actor_id=current.subject,
            operation="ticket.reply",
            resource_type="ticket",
            resource_id=ticket_id,
            outcome="success",
            details={"message_id": message.message_id},
            tenant_id=current.tenant_id,
        )
        return message

    @app.get("/v1/agent/audit/traces/{trace_id}", response_model=list[AuditEventView])
    def get_trace_audit(
        trace_id: str,
        current: Annotated[Principal, Depends(get_agent_principal)],
    ) -> list[AuditEventView]:
        return services.store.get_trace_audit(trace_id, tenant_id=current.tenant_id)

    app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app


def __getattr__(name: str):
    # Uvicorn resolves app lazily; importing factories must not open a database.
    if name == "app":
        return create_app()
    raise AttributeError(name)
