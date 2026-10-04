"""Trusted local demo personas; never take tenant or role from business payloads."""

import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    operator: bool = False

    @property
    def customer_id(self) -> str:
        if self.operator:
            raise PermissionError("customer_required")
        return self.subject


PERSONAS = {
    "customer-a1": Principal("customer-a1", "tenant-a"),
    "customer-a2": Principal("customer-a2", "tenant-a"),
    "operator-a": Principal("operator-a", "tenant-a", True),
    "customer-b1": Principal("customer-b1", "tenant-b"),
    "customer-b2": Principal("customer-b2", "tenant-b"),
    "operator-b": Principal("operator-b", "tenant-b", True),
}


@dataclass(frozen=True)
class DemoSession:
    principal: Principal
    csrf: str
    expires: datetime


class Sessions:
    def __init__(self) -> None:
        self.values: dict[str, DemoSession] = {}
        self.lock = threading.Lock()

    def create(self, persona: str, old: str | None = None) -> tuple[str, DemoSession]:
        if persona not in PERSONAS:
            raise ValueError("unknown_demo_persona")
        with self.lock:
            if old:
                self.values.pop(old, None)
            now = datetime.now(UTC)
            self.values = {k: v for k, v in self.values.items() if v.expires > now}
            token = secrets.token_urlsafe(32)
            session = DemoSession(
                PERSONAS[persona], secrets.token_urlsafe(32), now + timedelta(hours=2)
            )
            self.values[token] = session
            return token, session

    def get(self, token: str | None) -> DemoSession:
        with self.lock:
            value = self.values.get(token or "")
            if value is None or value.expires <= datetime.now(UTC):
                raise PermissionError("demo_session_required")
            return value
