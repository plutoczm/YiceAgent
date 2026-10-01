import json
from datetime import datetime, timezone
from uuid import uuid4

from redis import Redis
from redis.exceptions import WatchError

from backend.app.core.settings import Settings
from backend.app.domain.entities.chunk import ChunkMetadata, DocumentChunk
from backend.app.domain.entities.memory import (
    LongTermMemory,
    MemoryRule,
    MemoryRuleScope,
    MemorySnapshot,
)
from backend.app.domain.ports.embedding_provider import EmbeddingProvider
from backend.app.domain.ports.vector_store import VectorStore
from backend.app.infrastructure.vectorstore.exceptions import DocumentNotFoundError


class RedisChromaAgentMemory:
    """Redis short/rule memory plus Chroma semantic long-term memory."""

    def __init__(
        self,
        *,
        settings: Settings,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        redis_client: Redis | None = None,
    ) -> None:
        self.settings = settings
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.redis = redis_client or Redis.from_url(
            settings.memory.redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        self.prefix = settings.memory.key_prefix.rstrip(":")
        self.max_messages = settings.agent.memory_max_messages
        self.summary_chars = settings.agent.memory_summary_chars
        self.session_ttl = settings.memory.session_ttl_seconds
        self.collection = settings.memory.long_term_collection
        self.long_term_top_k = settings.memory.long_term_top_k
        self._collection_ready = False

    def load(self, session_id: str, *, user_id: str | None = None) -> MemorySnapshot:
        payload = self.redis.hgetall(self._session_key(session_id, user_id))
        messages = _load_json_list(payload.get("messages"))
        summary = payload.get("summary", "")
        if summary:
            messages.insert(
                0,
                {"role": "system", "content": f"Conversation summary: {summary}"},
            )
        return MemorySnapshot(messages=messages, summary=summary)

    def append_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        *,
        user_id: str | None = None,
    ) -> None:
        key = self._session_key(session_id, user_id)
        for _ in range(5):
            with self.redis.pipeline() as pipeline:
                try:
                    pipeline.watch(key)
                    payload = pipeline.hgetall(key)
                    messages = _load_json_list(payload.get("messages"))
                    summary = payload.get("summary", "")
                    messages.extend(
                        [
                            {"role": "user", "content": user_message},
                            {"role": "assistant", "content": assistant_message},
                        ]
                    )
                    if len(messages) > self.max_messages:
                        overflow = messages[: len(messages) - self.max_messages]
                        messages = messages[-self.max_messages :]
                        addition = " | ".join(
                            f"{item['role']}: {item['content']}" for item in overflow
                        )
                        summary = " | ".join(
                            part for part in (summary, addition) if part
                        )[-self.summary_chars :]
                    pipeline.multi()
                    pipeline.hset(
                        key,
                        mapping={
                            "messages": json.dumps(messages, ensure_ascii=False),
                            "summary": summary,
                            "user_id": user_id or "",
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    pipeline.expire(key, self.session_ttl)
                    pipeline.execute()
                    return
                except WatchError:
                    continue
        raise RuntimeError("Failed to update session memory after concurrent writes")

    def clear(self, session_id: str, *, user_id: str | None = None) -> bool:
        return bool(self.redis.delete(self._session_key(session_id, user_id)))

    def stats(self, session_id: str, *, user_id: str | None = None) -> dict[str, object]:
        snapshot = self.load(session_id, user_id=user_id)
        return {
            "backend": "redis_chroma",
            "short_term_messages": len(
                [item for item in snapshot.messages if item["role"] != "system"]
            ),
            "summary_available": bool(snapshot.summary),
            "summary_characters": len(snapshot.summary),
            "checkpoint_available": bool(
                self.load_state(session_id, user_id=user_id)
            ),
            "session_ttl_seconds": self.redis.ttl(self._session_key(session_id, user_id)),
        }

    def save_state(
        self,
        session_id: str,
        state: dict,
        *,
        user_id: str | None = None,
    ) -> None:
        key = self._session_key(session_id, user_id)
        self.redis.hset(
            key,
            mapping={
                "last_state": json.dumps(state, ensure_ascii=False),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.redis.expire(key, self.session_ttl)

    def load_state(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
    ) -> dict:
        value = self.redis.hget(self._session_key(session_id, user_id), "last_state")
        if not value:
            return {}
        payload = json.loads(value)
        return dict(payload) if isinstance(payload, dict) else {}

    async def recall_long_term(
        self,
        user_id: str,
        query: str,
        *,
        limit: int | None = None,
    ) -> list[LongTermMemory]:
        self._ensure_collection()
        embedding = await self.embedding_provider.embed_query(query)
        results = self.vector_store.similarity_search_with_scores(
            self.collection,
            embedding.embedding,
            limit=limit or self.long_term_top_k,
            metadata_filter={"owner_id": user_id},
        )
        return [
            LongTermMemory(
                id=item.chunk.id,
                user_id=user_id,
                content=item.chunk.content,
                created_at=item.chunk.metadata.created_at,
                score=item.score,
            )
            for item in results
        ]

    async def add_long_term(self, user_id: str, content: str) -> LongTermMemory:
        self._ensure_collection()
        memory_id = uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        embedding = await self.embedding_provider.embed_query(content)
        chunk = DocumentChunk(
            id=memory_id,
            document_id=memory_id,
            content=content,
            embedding=embedding.embedding,
            metadata=ChunkMetadata(
                document_id=memory_id,
                filename=f"memory-{memory_id}",
                file_type="memory",
                domain="agent_memory",
                chunk_index=0,
                created_at=created_at,
                source="explicit_user_memory",
                tags=["long_term_memory"],
                owner_id=user_id,
            ),
        )
        self.vector_store.add_documents(self.collection, [chunk])
        return LongTermMemory(
            id=memory_id,
            user_id=user_id,
            content=content,
            created_at=created_at,
        )

    async def list_long_term(self, user_id: str) -> list[LongTermMemory]:
        self._ensure_collection()
        chunks = self.vector_store.metadata_filter_search(
            self.collection,
            {"owner_id": user_id},
            limit=1000,
        )
        return [
            LongTermMemory(
                id=chunk.id,
                user_id=user_id,
                content=chunk.content,
                created_at=chunk.metadata.created_at,
            )
            for chunk in chunks
        ]

    def delete_long_term(self, user_id: str, memory_id: str) -> bool:
        self._ensure_collection()
        try:
            chunk = self.vector_store.get_document(self.collection, memory_id)
        except DocumentNotFoundError:
            return False
        if chunk.metadata.owner_id != user_id:
            return False
        self.vector_store.delete_documents(self.collection, [memory_id])
        return True

    def list_rules(self, user_id: str | None = None) -> list[MemoryRule]:
        payloads = list(self.redis.hvals(self._global_rule_key()))
        if user_id:
            payloads.extend(self.redis.hvals(self._user_rule_key(user_id)))
        rules = [MemoryRule.model_validate_json(payload) for payload in payloads]
        return sorted(
            [rule for rule in rules if rule.enabled],
            key=lambda item: item.priority,
            reverse=True,
        )

    def upsert_rule(self, rule: MemoryRule) -> MemoryRule:
        if rule.scope is MemoryRuleScope.USER and not rule.user_id:
            raise ValueError("user_id is required for user-scoped rules")
        key = (
            self._global_rule_key()
            if rule.scope is MemoryRuleScope.GLOBAL
            else self._user_rule_key(rule.user_id or "")
        )
        self.redis.hset(key, rule.id, rule.model_dump_json())
        return rule

    def delete_rule(self, rule_id: str, *, user_id: str | None = None) -> bool:
        key = self._user_rule_key(user_id) if user_id else self._global_rule_key()
        return bool(self.redis.hdel(key, rule_id))

    def _ensure_collection(self) -> None:
        if self._collection_ready:
            return
        if self.collection not in self.vector_store.list_collections():
            self.vector_store.create_collection(
                self.collection,
                metadata={"purpose": "agent long-term memory"},
            )
        self._collection_ready = True

    def _session_key(self, session_id: str, user_id: str | None) -> str:
        return f"{self.prefix}:session:{user_id or '_anonymous'}:{session_id}"

    def _global_rule_key(self) -> str:
        return f"{self.prefix}:rules:global"

    def _user_rule_key(self, user_id: str) -> str:
        return f"{self.prefix}:rules:user:{user_id}"


def _load_json_list(value: str | None) -> list[dict[str, str]]:
    if not value:
        return []
    payload = json.loads(value)
    if not isinstance(payload, list):
        return []
    return [dict(item) for item in payload if isinstance(item, dict)]
