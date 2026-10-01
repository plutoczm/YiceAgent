"""Opaque identities enabled only by the explicit local demo factory."""

import secrets

from app.auth import AuthenticationError, AuthorizationError, Principal

PERSONAS = {
    "customer-a1": ("tenant-a", "customer-a1", {"customer"}),
    "customer-a2": ("tenant-a", "customer-a2", {"customer"}),
    "operator-a": ("tenant-a", None, {"support_agent", "support_admin"}),
    "customer-b1": ("tenant-b", "customer-b1", {"customer"}),
}


class DemoPrincipal(Principal):
    def require_approver(self) -> None:
        if self.authentication_method != "demo-opaque" or "support_admin" not in self.roles:
            raise AuthorizationError("demo_admin_required")


class DemoIdentity:
    def __init__(self, tokens: dict[str, str]):
        if set(tokens) != set(PERSONAS) or len(set(tokens.values())) != len(tokens):
            raise ValueError("invalid_demo_identities")
        if any(len(value) < 32 for value in tokens.values()):
            raise ValueError("weak_demo_token")
        self.tokens = tokens

    def authenticate(self, *, authorization, **ignored) -> Principal:
        token = (authorization or "").removeprefix("Bearer ")
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthenticationError("bearer_token_required")
        for name, expected in self.tokens.items():
            if secrets.compare_digest(token, expected):
                tenant, customer, roles = PERSONAS[name]
                return DemoPrincipal(
                    subject=name,
                    tenant_id=tenant,
                    customer_id=customer,
                    roles=frozenset(roles),
                    authentication_method="demo-opaque",
                )
        raise AuthenticationError("invalid_demo_token")
