from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from app.auth import AuthenticationError, AuthService, Principal
from app.container import ServiceContainer, build_container
from app.tenancy import TenantId


class JwtTokenVerifier(TokenVerifier):
    def __init__(self, auth: AuthService) -> None:
        self.auth = auth

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            payload = self.auth.decode_jwt(token)
        except AuthenticationError:
            return None
        subject = str(payload.get("sub") or "")
        if not subject:
            return None
        scopes = [item for item in str(payload.get("scope") or "").split() if item]
        client_id = str(payload.get("azp") or payload.get("client_id") or subject)
        expires_at = payload.get("exp")
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(expires_at) if expires_at is not None else None,
            subject=subject,
            claims=dict(payload),
        )


def build_mcp_server(container: ServiceContainer | None = None) -> MCPServer:
    services = container or build_container()
    settings = services.settings
    if settings.mcp_auth_mode == "jwt":
        issuer = settings.mcp_issuer_url or settings.auth_issuer
        if not issuer:
            raise RuntimeError("MCP_ISSUER_URL or AUTH_ISSUER is required for MCP JWT auth")
        server = MCPServer(
            "SupportOps AI",
            token_verifier=JwtTokenVerifier(services.auth),
            auth=AuthSettings(
                issuer_url=AnyHttpUrl(issuer),
                resource_server_url=AnyHttpUrl(settings.mcp_resource_server_url),
                required_scopes=[settings.mcp_required_scope],
            ),
        )
    elif settings.mcp_auth_mode == "disabled":
        server = MCPServer("SupportOps AI")
    else:
        raise RuntimeError("unsupported_mcp_auth_mode")

    def current_principal(required_scope: str) -> Principal:
        token = get_access_token()
        if token is not None:
            principal = services.auth.principal_from_claims(token.claims or {})
        else:
            if settings.app_env != "development" or settings.mcp_auth_mode != "disabled":
                raise PermissionError("authenticated_mcp_request_required")
            principal = Principal(
                subject="mcp-dev",
                tenant_id=TenantId(settings.dev_tenant_id),
                customer_id=settings.mcp_dev_customer_id,
                roles=frozenset({"customer"}),
                scopes=frozenset({settings.mcp_required_scope, settings.mcp_write_scope}),
            )
        principal.require_scope(required_scope)
        return principal

    @server.tool()
    def search_support_knowledge(query: str) -> dict[str, object]:
        """Search trusted support policy evidence without executing business actions."""
        principal = current_principal(settings.mcp_required_scope)
        trace_id = services.orchestrator._trace_id()
        citations = services.knowledge.search(query)
        services.store.record_audit(
            trace_id=trace_id,
            actor_id=principal.subject,
            operation="mcp.knowledge.search",
            resource_type="knowledge",
            resource_id=None,
            outcome="success",
            details={"citation_count": len(citations)},
            tenant_id=principal.tenant_id,
        )
        return {
            "trace_id": trace_id,
            "results": [item.model_dump() for item in citations],
        }

    @server.tool()
    def get_order(order_id: str) -> dict[str, object]:
        """Read one order owned by the authenticated customer."""
        principal = current_principal(settings.mcp_required_scope)
        customer_id = principal.require_customer()
        trace_id = services.orchestrator._trace_id()
        order = services.tools.get_order(
            order_id=order_id.upper(),
            customer_id=customer_id,
            trace_id=trace_id,
            actor_id=principal.subject,
            tenant_id=principal.tenant_id,
        )
        if order is None:
            return {"trace_id": trace_id, "found": False}
        return {"trace_id": trace_id, "found": True, "order": order.model_dump()}

    @server.tool()
    def request_refund(order_id: str, conversation_id: str) -> dict[str, object]:
        """Prepare a refund request; app-side customer confirmation is still mandatory."""
        principal = current_principal(settings.mcp_write_scope)
        customer_id = principal.require_customer()
        response = services.orchestrator.prepare_refund(
            customer_id=customer_id,
            actor_id=principal.subject,
            conversation_id=conversation_id,
            order_id=order_id.upper(),
            tenant_id=principal.tenant_id,
        )
        return response.model_dump(mode="json")

    @server.tool()
    def get_ticket(ticket_id: str) -> dict[str, object]:
        """Read a ticket only when it belongs to the authenticated customer."""
        principal = current_principal(settings.mcp_required_scope)
        customer_id = principal.require_customer()
        ticket = services.store.get_ticket(
            ticket_id, tenant_id=principal.tenant_id, customer_id=customer_id
        )
        if ticket is None or ticket.customer_id != customer_id:
            return {"found": False}
        return {"found": True, "ticket": ticket.model_dump(mode="json")}

    return server


def __getattr__(name: str):
    if name == "mcp":
        return build_mcp_server()
    raise AttributeError(name)


if __name__ == "__main__":
    build_mcp_server().run(transport="streamable-http")
