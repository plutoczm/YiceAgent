from typing import Protocol

from backend.app.domain.entities.memory import (
    LongTermMemory,
    MemoryRule,
    MemorySnapshot,
)


class AgentMemory(Protocol):
    def load(self, session_id: str, *, user_id: str | None = None) -> MemorySnapshot:
        raise NotImplementedError

    def append_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        *,
        user_id: str | None = None,
    ) -> None:
        raise NotImplementedError

    def clear(self, session_id: str, *, user_id: str | None = None) -> bool:
        raise NotImplementedError

    def stats(self, session_id: str, *, user_id: str | None = None) -> dict:
        raise NotImplementedError

    def save_state(
        self,
        session_id: str,
        state: dict,
        *,
        user_id: str | None = None,
    ) -> None:
        raise NotImplementedError

    def load_state(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
    ) -> dict:
        raise NotImplementedError

    async def recall_long_term(
        self,
        user_id: str,
        query: str,
        *,
        limit: int | None = None,
    ) -> list[LongTermMemory]:
        raise NotImplementedError

    async def add_long_term(self, user_id: str, content: str) -> LongTermMemory:
        raise NotImplementedError

    async def list_long_term(self, user_id: str) -> list[LongTermMemory]:
        raise NotImplementedError

    def delete_long_term(self, user_id: str, memory_id: str) -> bool:
        raise NotImplementedError

    def list_rules(self, user_id: str | None = None) -> list[MemoryRule]:
        raise NotImplementedError

    def upsert_rule(self, rule: MemoryRule) -> MemoryRule:
        raise NotImplementedError

    def delete_rule(self, rule_id: str, *, user_id: str | None = None) -> bool:
        raise NotImplementedError
