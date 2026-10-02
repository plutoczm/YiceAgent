from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from backend.app.domain.entities.memory import (
    LongTermMemory,
    MemoryRule,
    MemoryRuleScope,
    MemorySnapshot,
)


@dataclass
class _SessionMemory:
    messages: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""
    last_state: dict = field(default_factory=dict)


class ConversationMemory:
    """单进程开发/测试 memory；生产多实例使用 RedisChromaAgentMemory。"""

    def __init__(self, *, max_messages: int = 12, summary_chars: int = 1200) -> None:
        if max_messages < 2:
            raise ValueError("max_messages must be at least 2")
        self.max_messages = max_messages
        self.summary_chars = summary_chars
        self._sessions: dict[str, _SessionMemory] = {}
        self._long_term: dict[str, list[LongTermMemory]] = {}
        self._rules: dict[str, MemoryRule] = {}
        self._lock = RLock()

    def load(self, session_id: str, *, user_id: str | None = None) -> MemorySnapshot:
        with self._lock:
            memory = self._sessions.get(self._session_key(session_id, user_id), _SessionMemory())
            messages = [dict(item) for item in memory.messages]
            if memory.summary:
                messages.insert(
                    0,
                    {"role": "system", "content": f"Conversation summary: {memory.summary}"},
                )
            return MemorySnapshot(messages=messages, summary=memory.summary)

    def append_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        *,
        user_id: str | None = None,
    ) -> None:
        with self._lock:
            memory = self._sessions.setdefault(
                self._session_key(session_id, user_id), _SessionMemory()
            )
            memory.messages.extend(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": assistant_message},
                ]
            )
            if len(memory.messages) > self.max_messages:
                overflow = memory.messages[: len(memory.messages) - self.max_messages]
                memory.messages = memory.messages[-self.max_messages :]
                addition = " | ".join(
                    f"{item['role']}: {item['content']}" for item in overflow
                )
                memory.summary = " | ".join(
                    part for part in (memory.summary, addition) if part
                )[-self.summary_chars :]

    def clear(self, session_id: str, *, user_id: str | None = None) -> bool:
        with self._lock:
            return self._sessions.pop(self._session_key(session_id, user_id), None) is not None

    def stats(self, session_id: str, *, user_id: str | None = None) -> dict[str, object]:
        snapshot = self.load(session_id, user_id=user_id)
        return {
            "backend": "in_memory",
            "short_term_messages": len(
                [item for item in snapshot.messages if item["role"] != "system"]
            ),
            "summary_available": bool(snapshot.summary),
            "summary_characters": len(snapshot.summary),
            "checkpoint_available": bool(
                self.load_state(session_id, user_id=user_id)
            ),
        }

    def save_state(
        self,
        session_id: str,
        state: dict,
        *,
        user_id: str | None = None,
    ) -> None:
        with self._lock:
            memory = self._sessions.setdefault(
                self._session_key(session_id, user_id), _SessionMemory()
            )
            memory.last_state = dict(state)

    def load_state(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
    ) -> dict:
        with self._lock:
            memory = self._sessions.get(self._session_key(session_id, user_id))
            return dict(memory.last_state) if memory else {}

    async def recall_long_term(
        self,
        user_id: str,
        query: str,
        *,
        limit: int | None = None,
    ) -> list[LongTermMemory]:
        terms = set(query.lower().split())
        ranked = sorted(
            self._long_term.get(user_id, []),
            key=lambda item: len(terms & set(item.content.lower().split())),
            reverse=True,
        )
        return ranked[: limit or 5]

    async def add_long_term(self, user_id: str, content: str) -> LongTermMemory:
        memory = LongTermMemory(
            id=uuid4().hex,
            user_id=user_id,
            content=content,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._long_term.setdefault(user_id, []).append(memory)
        return memory

    async def list_long_term(self, user_id: str) -> list[LongTermMemory]:
        return list(self._long_term.get(user_id, []))

    def delete_long_term(self, user_id: str, memory_id: str) -> bool:
        memories = self._long_term.get(user_id, [])
        remaining = [item for item in memories if item.id != memory_id]
        self._long_term[user_id] = remaining
        return len(remaining) != len(memories)

    def list_rules(self, user_id: str | None = None) -> list[MemoryRule]:
        rules = [
            rule
            for rule in self._rules.values()
            if rule.enabled
            and (
                rule.scope is MemoryRuleScope.GLOBAL
                or (rule.scope is MemoryRuleScope.USER and rule.user_id == user_id)
            )
        ]
        return sorted(rules, key=lambda item: item.priority, reverse=True)

    def upsert_rule(self, rule: MemoryRule) -> MemoryRule:
        self._rules[self._rule_key(rule.id, rule.user_id)] = rule
        return rule

    def delete_rule(self, rule_id: str, *, user_id: str | None = None) -> bool:
        return self._rules.pop(self._rule_key(rule_id, user_id), None) is not None

    def _session_key(self, session_id: str, user_id: str | None) -> str:
        return f"{user_id or '_anonymous'}:{session_id}"

    def _rule_key(self, rule_id: str, user_id: str | None) -> str:
        return f"{user_id or '_global'}:{rule_id}"


__all__ = ["ConversationMemory", "MemorySnapshot"]
