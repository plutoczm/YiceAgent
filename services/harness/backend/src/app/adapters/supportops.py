"""Fixed HTTP routes, safe errors and validated SupportOps wire results."""

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx2
from pydantic import TypeAdapter, ValidationError

from app.adapters.supportops_dto import (
    ConfirmationResponse,
    OperationView,
    OrderView,
    SupportResponse,
    TicketMessageView,
    TicketView,
)
from app.demo.identity import Identity
from app.workflows.models import Confirm, Error, Lookup, Message, Payload, Review, Ticket


@dataclass
class DomainFailure(Exception):
    """Only categories survive the adapter; bodies never become errors."""

    category: Error
    unknown: bool = False


@dataclass
class DispatchState:
    """Per-run in-memory evidence of a write attempt and acknowledged outcome."""

    write_started: bool = False
    outcome_known: bool = False
    result: Any = None


class SupportOpsAdapter:
    """One fixed loopback service; no retries, redirects or ambient proxies."""

    def __init__(
        self,
        tokens: dict[str, str],
        base_url: str = "http://127.0.0.1:8011",
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """Construct an owned client for the fixed demo service."""
        if base_url != "http://127.0.0.1:8011":
            raise ValueError("unsupported_service_target")
        self.tokens = tokens
        self.client = httpx2.AsyncClient(
            base_url=base_url,
            timeout=30,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def close(self) -> None:
        """Release the owned client."""
        await self.client.aclose()

    @staticmethod
    def route(
        payload: Payload, identity: Identity
    ) -> tuple[str, str, dict[str, object] | None, Any]:
        """Compile a closed action into an allowlisted route and response DTO."""
        if isinstance(payload, Lookup):
            resource = "orders" if payload.kind == "order" else "operations"
            model = OrderView if payload.kind == "order" else OperationView
            return "GET", f"/v1/{resource}/{payload.object_id}", None, model
        if isinstance(payload, Message):
            if payload.action == "message":
                return (
                    "POST",
                    "/v1/support/messages",
                    {
                        "message": payload.message,
                        "conversation_id": payload.conversation_id,
                    },
                    SupportResponse,
                )
            return (
                "POST",
                "/internal/demo/actions",
                payload.model_dump(exclude_none=True),
                SupportResponse,
            )
        if isinstance(payload, Confirm):
            return (
                "POST",
                f"/v1/actions/{payload.action_id}/confirm",
                {"confirm": payload.confirm},
                ConfirmationResponse,
            )
        if isinstance(payload, Review):
            return (
                "POST",
                f"/v1/agent/operations/{payload.operation_id}/decision",
                {
                    "decision": payload.decision,
                    "expected_version": payload.expected_version,
                },
                OperationView,
            )
        assert isinstance(payload, Ticket)
        prefix = "/v1/agent/tickets" if identity.operator else "/v1/tickets"
        if payload.action == "list":
            return "GET", prefix, None, list[TicketView]
        path = f"{prefix}/{payload.ticket_id}"
        if payload.action == "get":
            return "GET", path, None, TicketView
        if payload.action == "messages":
            return "GET", path + "/messages", None, list[TicketMessageView]
        if payload.action == "operation":
            return "GET", path + "/operation", None, OperationView
        if payload.action == "reply":
            return "POST", path + "/messages", {"message": payload.message}, TicketMessageView
        if payload.action == "assign":
            return "POST", path + "/assign", {"assignee_id": identity.name}, TicketView
        return "POST", path + "/transition", {"status": payload.status}, TicketView

    async def invoke(
        self,
        payload: Payload,
        identity: Identity,
        *,
        dispatch: DispatchState | None = None,
    ) -> Any:
        """Perform exactly one request and validate the domain response."""
        method, path, body, model = self.route(payload, identity)
        headers = {"Authorization": "Bearer " + self.tokens[identity.name]}
        dispatch = dispatch if dispatch is not None else DispatchState()
        try:
            # Mark only after routing/token preparation, when handing off the request.
            dispatch.write_started = method != "GET"
            response = await asyncio.wait_for(
                self.client.request(
                    method,
                    path,
                    json=body,
                    headers=headers,
                ),
                timeout=30,
            )
        except (httpx2.TimeoutException, TimeoutError):
            raise DomainFailure(Error.TIMEOUT, method != "GET") from None
        except httpx2.RequestError:
            # A lost response to a write cannot prove that the write failed.
            raise DomainFailure(Error.UNAVAILABLE, method != "GET") from None
        if response.status_code >= 500:
            raise DomainFailure(Error.UNAVAILABLE, method != "GET")
        if response.status_code >= 300:
            dispatch.outcome_known = True
        if response.status_code in {401, 403}:
            raise DomainFailure(Error.AUTHORIZATION)
        if response.status_code >= 300:
            raise DomainFailure(Error.REJECTED)
        try:
            result = TypeAdapter(model).validate_python(response.json())
        except (ValueError, ValidationError):
            raise DomainFailure(Error.INVALID_RESPONSE, method != "GET") from None
        dispatch.result = result
        dispatch.outcome_known = True
        return result
