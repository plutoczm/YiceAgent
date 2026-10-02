from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from backend.app.application.rag.chunking_service import ChunkingService
from backend.app.application.rag.citation_service import CitationService
from backend.app.application.rag.document_ingestion_service import DocumentIngestionService
from backend.app.application.rag.rag_service import RAGService
from backend.app.application.rag.retrieval_service import RetrievalService
from backend.app.domain.entities.chunk import DocumentChunk
from backend.app.domain.ports.llm_provider import (
    LLMHealthStatus,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    LLMUsage,
)
from backend.app.domain.ports.vector_store import CollectionStats, VectorSearchResult
from backend.app.infrastructure.document_loaders import DocumentLoaderFactory
from backend.app.infrastructure.embeddings.embedding_provider import EmbeddingProvider
from backend.app.infrastructure.embeddings.models import EmbeddingResult


pytestmark = pytest.mark.anyio


class FakeEmbeddingProvider(EmbeddingProvider):
    def provider_name(self) -> str:
        return "fake-embedding"

    def embedding_dimension(self) -> int:
        return 3

    async def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResult]:
        return [
            EmbeddingResult(
                text=text,
                embedding=vector_for_text(text),
                model="fake-model",
                token_count=len(text.split()),
            )
            for text in texts
        ]

    async def embed_query(self, text: str) -> EmbeddingResult:
        return EmbeddingResult(
            text=text,
            embedding=vector_for_text(text),
            model="fake-model",
            token_count=len(text.split()),
        )


class FakeVectorStore:
    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.documents: dict[str, list[DocumentChunk]] = {}

    def create_collection(self, name: str, metadata=None) -> None:
        self.collections.add(name)
        self.documents.setdefault(name, [])

    def delete_collection(self, name: str) -> None:
        self.collections.remove(name)
        self.documents.pop(name, None)

    def list_collections(self) -> list[str]:
        return sorted(self.collections)

    def add_documents(self, collection_name: str, chunks, *, batch_size: int = 100) -> None:
        self.create_collection(collection_name)
        self.documents[collection_name].extend(chunks)

    def update_documents(self, collection_name: str, chunks, *, batch_size: int = 100) -> None:
        existing = {chunk.id: chunk for chunk in self.documents.get(collection_name, [])}
        existing.update({chunk.id: chunk for chunk in chunks})
        self.documents[collection_name] = list(existing.values())

    def delete_documents(self, collection_name: str, ids, *, batch_size: int = 100) -> None:
        self.documents[collection_name] = [
            chunk for chunk in self.documents.get(collection_name, []) if chunk.id not in ids
        ]

    def list_documents(self, collection_name: str, *, limit: int = 100, offset: int = 0):
        return self.documents.get(collection_name, [])[offset : offset + limit]

    def get_document(self, collection_name: str, document_id: str):
        for chunk in self.documents.get(collection_name, []):
            if chunk.id == document_id:
                return chunk
        raise KeyError(document_id)

    def similarity_search(self, collection_name: str, query_vector, *, limit: int = 5, metadata_filter=None):
        return [
            result.chunk
            for result in self.similarity_search_with_scores(
                collection_name,
                query_vector,
                limit=limit,
                metadata_filter=metadata_filter,
            )
        ]

    def similarity_search_with_scores(
        self,
        collection_name: str,
        query_vector,
        *,
        limit: int = 5,
        metadata_filter=None,
    ):
        candidates = [
            chunk
            for chunk in self.documents.get(collection_name, [])
            if metadata_matches(chunk, metadata_filter)
        ]
        scored = [
            VectorSearchResult(
                chunk=chunk,
                score=cosine_similarity(query_vector, chunk.embedding),
            )
            for chunk in candidates
        ]
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]

    def metadata_filter_search(self, collection_name: str, metadata_filter, *, limit: int = 100, offset: int = 0):
        matches = [
            chunk
            for chunk in self.documents.get(collection_name, [])
            if metadata_matches(chunk, metadata_filter)
        ]
        return matches[offset : offset + limit]

    def collection_stats(self, collection_name: str) -> CollectionStats:
        return CollectionStats(
            name=collection_name,
            document_count=len(self.documents.get(collection_name, [])),
        )


class FakeLLMProvider:
    def provider_name(self) -> str:
        return "fake-llm"

    def token_count(self, text_or_messages) -> int:
        if isinstance(text_or_messages, str):
            return len(text_or_messages.split())
        return sum(len(message.content.split()) for message in text_or_messages)

    async def chat(self, messages: Sequence[LLMMessage], *, temperature=None, max_tokens=None) -> LLMResponse:
        prompt = messages[-1].content
        assert "Answer only using the retrieved context" in prompt
        assert "Spark AQE" in prompt
        return LLMResponse(
            provider="fake-llm",
            model="fake-chat",
            content="Spark AQE reduces shuffle work by adapting plans at runtime.",
            usage=LLMUsage(prompt_tokens=20, completion_tokens=9, total_tokens=29),
        )

    async def stream_chat(self, messages: Sequence[LLMMessage], *, temperature=None, max_tokens=None) -> AsyncIterator[LLMStreamChunk]:
        yield LLMStreamChunk(provider="fake-llm", model="fake-chat", content="unused")

    async def health_check(self) -> LLMHealthStatus:
        return LLMHealthStatus(
            provider="fake-llm",
            ok=True,
            api_key_configured=True,
            reachable=True,
            model_available=True,
            model="fake-chat",
            message="ok",
        )


def test_chunking_service_recursive_overlap() -> None:
    service = ChunkingService(chunk_size=30, chunk_overlap=8)

    chunks = service.chunk_text("Spark AQE improves joins.\nKafka handles streams.", document_id="doc")

    assert len(chunks) >= 2
    assert chunks[0].chunk_index == 0
    assert chunks[0].text
    assert all(len(chunk.text) <= 30 for chunk in chunks)


async def test_txt_and_markdown_ingestion_store_vectors(tmp_path: Path) -> None:
    uploads_dir = tmp_path / "uploads"
    txt_path = tmp_path / "spark.txt"
    md_path = tmp_path / "kafka.md"
    txt_path.write_text("Spark AQE reduces shuffle and improves joins.", encoding="utf-8")
    md_path.write_text("# Kafka\n\nKafka consumer lag can indicate slow processing.", encoding="utf-8")
    vector_store = FakeVectorStore()
    ingestion = DocumentIngestionService(
        loader_factory=DocumentLoaderFactory(),
        chunking_service=ChunkingService(chunk_size=80, chunk_overlap=10),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=vector_store,
        uploads_dir=uploads_dir,
    )

    txt_result = await ingestion.ingest_file(
        txt_path,
        collection_name="kb",
        domain="spark",
        tags=["spark", "aqe"],
    )
    md_result = await ingestion.ingest_file(
        md_path,
        collection_name="kb",
        domain="kafka",
        tags=["kafka"],
    )

    assert txt_result.chunk_count == 1
    assert md_result.chunk_count == 1
    assert (uploads_dir / "spark.txt").is_file()
    assert (uploads_dir / "kafka.md").is_file()
    assert vector_store.collection_stats("kb").document_count == 2


async def test_retrieval_supports_top_k_filter_and_threshold(tmp_path: Path) -> None:
    vector_store = FakeVectorStore()
    ingestion = DocumentIngestionService(
        loader_factory=DocumentLoaderFactory(),
        chunking_service=ChunkingService(chunk_size=100, chunk_overlap=0),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=vector_store,
        uploads_dir=tmp_path / "uploads",
    )
    spark_path = tmp_path / "spark.txt"
    mysql_path = tmp_path / "mysql.txt"
    spark_path.write_text("Spark AQE reduces shuffle.", encoding="utf-8")
    mysql_path.write_text("MySQL indexes speed up filters.", encoding="utf-8")
    await ingestion.ingest_file(spark_path, collection_name="kb", domain="spark")
    await ingestion.ingest_file(mysql_path, collection_name="kb", domain="mysql")
    retrieval = RetrievalService(
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=vector_store,
    )

    results = await retrieval.retrieve(
        "How does Spark AQE help?",
        collection_name="kb",
        top_k=2,
        metadata_filter={"domain": "spark"},
        score_threshold=0.1,
    )

    assert len(results) == 1
    assert results[0].chunk.metadata.domain == "spark"
    assert results[0].score >= 0.1


async def test_hybrid_retrieval_uses_bm25_when_dense_scores_tie(tmp_path: Path) -> None:
    vector_store = FakeVectorStore()
    ingestion = DocumentIngestionService(
        loader_factory=DocumentLoaderFactory(),
        chunking_service=ChunkingService(chunk_size=100, chunk_overlap=0),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=vector_store,
        uploads_dir=tmp_path / "uploads",
    )
    distractor = tmp_path / "distractor.txt"
    relevant = tmp_path / "hive.txt"
    distractor.write_text("A generic unrelated document.", encoding="utf-8")
    relevant.write_text("Hive partition pruning reduces scanned data.", encoding="utf-8")
    await ingestion.ingest_file(distractor, collection_name="kb", domain="general")
    await ingestion.ingest_file(relevant, collection_name="kb", domain="hive")
    retrieval = RetrievalService(
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=vector_store,
    )

    results = await retrieval.retrieve(
        "How does partition pruning work?",
        collection_name="kb",
        top_k=1,
        retrieval_mode="hybrid",
    )

    assert results[0].chunk.metadata.filename == "hive.txt"
    assert results[0].lexical_score > 0


async def test_citation_generation_and_end_to_end_rag(tmp_path: Path) -> None:
    vector_store = FakeVectorStore()
    embedding_provider = FakeEmbeddingProvider()
    ingestion = DocumentIngestionService(
        loader_factory=DocumentLoaderFactory(),
        chunking_service=ChunkingService(chunk_size=100, chunk_overlap=0),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        uploads_dir=tmp_path / "uploads",
    )
    spark_path = tmp_path / "spark.md"
    spark_path.write_text("# Spark AQE\n\nSpark AQE reduces shuffle work.", encoding="utf-8")
    await ingestion.ingest_file(
        spark_path,
        collection_name="kb",
        domain="spark",
        tags=["spark", "aqe"],
    )
    retrieval = RetrievalService(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    citation_service = CitationService()
    rag = RAGService(
        retrieval_service=retrieval,
        citation_service=citation_service,
        llm_provider=FakeLLMProvider(),
    )

    response = await rag.answer(
        "What does Spark AQE do?",
        collection_name="kb",
        top_k=3,
        metadata_filter={"domain": "spark"},
    )

    assert "Spark AQE reduces shuffle" in response.answer
    assert response.citations
    assert response.citations[0].document_name == "spark.md"
    assert response.citations[0].chunk_reference.endswith("#chunk-0")
    assert response.retrieved_chunks[0].metadata.source == "spark.md"
    assert response.token_usage.total_tokens == 29


def vector_for_text(text: str) -> list[float]:
    lowered = text.lower()
    return [
        1.0 if "spark" in lowered or "aqe" in lowered else 0.0,
        1.0 if "kafka" in lowered else 0.0,
        1.0 if "mysql" in lowered else 0.0,
    ]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def metadata_matches(chunk: DocumentChunk, metadata_filter) -> bool:
    if not metadata_filter:
        return True
    metadata = chunk.metadata.model_dump()
    return all(metadata.get(key) == value for key, value in metadata_filter.items())
