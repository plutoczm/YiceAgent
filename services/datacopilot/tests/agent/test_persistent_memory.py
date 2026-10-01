from collections.abc import Sequence
from pathlib import Path

import fakeredis
import pytest

from backend.app.core.settings import Settings
from backend.app.domain.entities.memory import MemoryRule, MemoryRuleScope
from backend.app.infrastructure.embeddings.embedding_provider import EmbeddingProvider
from backend.app.infrastructure.embeddings.models import EmbeddingResult
from backend.app.infrastructure.memory import RedisChromaAgentMemory
from backend.app.infrastructure.vectorstore import ChromaDBVectorStore


pytestmark = pytest.mark.anyio


class FakeEmbeddingProvider(EmbeddingProvider):
    async def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResult]:
        return [await self.embed_query(text) for text in texts]

    async def embed_query(self, text: str) -> EmbeddingResult:
        lowered = text.lower()
        return EmbeddingResult(
            text=text,
            embedding=[
                1.0 if "spark" in lowered else 0.0,
                1.0 if "hive" in lowered else 0.0,
                1.0 if "mysql" in lowered else 0.0,
            ],
            model="fake",
            token_count=1,
        )

    def provider_name(self) -> str:
        return "fake"

    def embedding_dimension(self) -> int:
        return 3


def make_settings(tmp_path: Path) -> Settings:
    project_root = tmp_path / "project"
    return Settings(
        _env_file=None,
        environment="test",
        paths={
            "project_root": project_root,
            "data_dir": "data",
            "chromadb_dir": "data/chromadb",
            "uploads_dir": "data/uploads",
            "logs_dir": "data/logs",
            "cache_dir": "data/cache",
            "embeddings_dir": "data/embeddings",
            "temp_dir": "data/temp",
            "models_dir": "models",
        },
        memory={
            "backend": "redis",
            "key_prefix": "test:memory",
            "session_ttl_seconds": 3600,
            "long_term_collection": "agent_memory_test",
            "long_term_top_k": 3,
        },
        agent={"memory_max_messages": 4, "memory_summary_chars": 200},
        logging={"file_enabled": False},
    )


def make_memory_pair(tmp_path: Path):
    settings = make_settings(tmp_path)
    server = fakeredis.FakeServer()
    vector_store = ChromaDBVectorStore(settings)
    embedding = FakeEmbeddingProvider()
    first = RedisChromaAgentMemory(
        settings=settings,
        vector_store=vector_store,
        embedding_provider=embedding,
        redis_client=fakeredis.FakeRedis(server=server, decode_responses=True),
    )
    second = RedisChromaAgentMemory(
        settings=settings,
        vector_store=vector_store,
        embedding_provider=embedding,
        redis_client=fakeredis.FakeRedis(server=server, decode_responses=True),
    )
    return first, second


def test_short_term_memory_is_shared_across_instances(tmp_path: Path) -> None:
    first, second = make_memory_pair(tmp_path)

    first.append_turn(
        "session-1",
        "first question",
        "first answer",
        user_id="user-1",
    )
    first.save_state(
        "session-1",
        {"intent": "RAG", "routing_path": ["planner", "rag"]},
        user_id="user-1",
    )

    snapshot = second.load("session-1", user_id="user-1")
    assert [item["content"] for item in snapshot.messages] == [
        "first question",
        "first answer",
    ]
    assert second.stats("session-1", user_id="user-1")["backend"] == "redis_chroma"
    assert second.stats("session-1", user_id="user-1")["session_ttl_seconds"] > 0
    assert second.load_state("session-1", user_id="user-1")["intent"] == "RAG"
    assert second.stats("session-1", user_id="user-1")["checkpoint_available"] is True


async def test_long_term_memory_is_semantic_and_user_isolated(tmp_path: Path) -> None:
    first, second = make_memory_pair(tmp_path)
    spark = await first.add_long_term("user-1", "Spark AQE is my current focus")
    await first.add_long_term("user-2", "MySQL indexing is my current focus")

    recalled = await second.recall_long_term("user-1", "Spark tuning", limit=2)
    listed = await second.list_long_term("user-1")

    assert [item.id for item in recalled] == [spark.id]
    assert [item.id for item in listed] == [spark.id]
    assert second.delete_long_term("user-2", spark.id) is False
    assert second.delete_long_term("user-1", spark.id) is True


def test_rule_memory_merges_global_and_user_rules_by_priority(tmp_path: Path) -> None:
    first, second = make_memory_pair(tmp_path)
    first.upsert_rule(
        MemoryRule(
            id="global-safe",
            content="Never generate write SQL.",
            scope=MemoryRuleScope.GLOBAL,
            priority=1000,
        )
    )
    first.upsert_rule(
        MemoryRule(
            id="user-engine",
            content="Prefer Spark SQL.",
            scope=MemoryRuleScope.USER,
            user_id="user-1",
            priority=500,
        )
    )

    rules = second.list_rules("user-1")

    assert [rule.id for rule in rules] == ["global-safe", "user-engine"]
    assert second.list_rules("user-2")[0].id == "global-safe"
    assert second.delete_rule("user-engine", user_id="user-1") is True
