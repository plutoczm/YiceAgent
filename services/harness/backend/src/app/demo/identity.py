"""Server-owned local sessions for the explicit demo application."""

import secrets
from dataclasses import dataclass

from fastapi import HTTPException


@dataclass(frozen=True)
class Identity:
    """Fixed demo ownership context."""

    name: str
    tenant: str
    operator: bool = False


PERSONAS = {
    "customer-a1": Identity("customer-a1", "tenant-a"),
    "customer-a2": Identity("customer-a2", "tenant-a"),
    "operator-a": Identity("operator-a", "tenant-a", True),
    "customer-b1": Identity("customer-b1", "tenant-b"),
}


class Sessions:
    """Unpredictable handles; switching revokes the previous context."""

    def __init__(self) -> None:
        """Start with no authenticated session."""
        self.handles: dict[str, Identity] = {}

    def switch(self, persona: str, previous: str | None) -> str:
        """Select only a predeclared identity."""
        if persona not in PERSONAS:
            raise HTTPException(422, "unknown_demo_persona")
        if previous:
            self.handles.pop(previous, None)
        handle = secrets.token_urlsafe(32)
        self.handles[handle] = PERSONAS[persona]
        return handle

    def get(self, handle: str | None) -> Identity:
        """Resolve a current local session without accepting caller roles."""
        if handle is None or handle not in self.handles:
            raise HTTPException(401, "demo_session_required")
        return self.handles[handle]
