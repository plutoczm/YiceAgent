from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import jwt
from jwt import PyJWKClient

from app.config import Settings
from app.tenancy import TenantId


class AuthenticationError(ValueError):
    pass


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    tenant_id: TenantId
    customer_id: str | None
    roles: frozenset[str]
    scopes: frozenset[str] = field(default_factory=frozenset)
    authentication_method: str = "dev"

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", TenantId(self.tenant_id))
        if not isinstance(self.subject, str) or not self.subject.strip() or len(self.subject) > 128:
            raise ValueError("invalid_subject")
        if self.customer_id is not None and (
            not isinstance(self.customer_id, str)
            or not self.customer_id.strip()
            or len(self.customer_id) > 128
        ):
            raise ValueError("invalid_customer_identity")
        if "customer" in self.roles and self.customer_id is None:
            raise ValueError("customer_identity_required")

    def require_customer(self) -> str:
        if not self.customer_id:
            raise AuthorizationError("customer_identity_required")
        return self.customer_id

    def require_agent(self) -> None:
        if not self.roles.intersection({"support_agent", "support_admin"}):
            raise AuthorizationError("support_agent_role_required")

    def require_approver(self) -> None:
        if self.authentication_method != "jwt" or "support_admin" not in self.roles:
            raise AuthorizationError("jwt_support_admin_required")

    def require_scope(self, scope: str) -> None:
        if scope not in self.scopes:
            raise AuthorizationError("insufficient_scope")


class AuthService:
    def __init__(self, settings: Settings) -> None:
        settings.validate_security()
        self.settings = settings
        self._jwks_client = PyJWKClient(settings.auth_jwks_url) if settings.auth_jwks_url else None

    def authenticate(
        self,
        *,
        authorization: str | None,
        dev_principal_id: str | None,
        dev_customer_id: str | None,
        dev_roles: str | None,
    ) -> Principal:
        if self.settings.auth_mode == "dev":
            if authorization:
                raise AuthenticationError("conflicting_identity_sources")
            if not dev_principal_id:
                raise AuthenticationError("x-principal-id header required in dev auth mode")
            try:
                return Principal(
                    subject=dev_principal_id,
                    tenant_id=TenantId(self.settings.dev_tenant_id),
                    customer_id=dev_customer_id,
                    roles=frozenset(self.parse_roles(dev_roles or "customer")),
                )
            except ValueError as exc:
                raise AuthenticationError("invalid_development_identity") from exc

        if not authorization or not authorization.startswith("Bearer "):
            raise AuthenticationError("bearer_token_required")
        # JWT is the sole authority. Any development identity headers are ignored.
        return self.principal_from_claims(
            self.decode_jwt(authorization.removeprefix("Bearer ").strip())
        )

    def principal_from_claims(self, payload: dict[str, Any]) -> Principal:
        if "tenant_id" not in payload:
            raise AuthenticationError("token_tenant_required")
        try:
            tenant = TenantId(payload["tenant_id"])
        except ValueError as exc:
            raise AuthenticationError("invalid_token_tenant") from exc
        try:
            roles = frozenset(self.parse_roles(payload.get("roles")))
            scope = payload.get("scope", "")
            if not isinstance(scope, str):
                raise ValueError("invalid_scope")
            return Principal(
                subject=payload.get("sub"),
                tenant_id=tenant,
                customer_id=payload.get("customer_id"),
                roles=roles,
                scopes=frozenset(scope.split()),
                authentication_method="jwt",
            )
        except ValueError as exc:
            raise AuthenticationError("invalid_token_identity") from exc

    def decode_jwt(self, token: str) -> dict[str, Any]:
        if not (self._jwks_client and self.settings.auth_issuer and self.settings.auth_audience):
            raise AuthenticationError("jwt_auth_not_configured")
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=[self.settings.auth_algorithm],
                audience=self.settings.auth_audience,
                issuer=self.settings.auth_issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid_access_token") from exc
        claims = dict(payload)
        self.principal_from_claims(claims)
        return claims

    @staticmethod
    def parse_roles(value: object) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return {item.strip() for item in value if item.strip()}
        if isinstance(value, str):
            return set(value.replace(",", " ").split())
        raise ValueError("invalid_roles")
