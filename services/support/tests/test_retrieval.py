import httpx
import pytest

from app.config import Settings
from app.container import build_container
from app.knowledge import KnowledgeService, default_knowledge_documents
from app.knowledge_ingestion import (
    KnowledgeChunker,
    KnowledgeSourceDocument,
    build_retrieval_documents,
)
from app.retrieval import (
    BM25Retriever,
    DeterministicHashEmbedding,
    HybridRetriever,
    OpenAICompatibleEmbedding,
    RetrievalBackendError,
    RetrievalFailureReason,
)


def test_hybrid_retrieval_prefers_shipping_evidence():
    service = KnowledgeService()
    citations = service.search("where can I find package tracking after shipment")
    assert citations
    assert citations[0].document_id == "KB-SHIPPING-01"
    assert citations[0].score >= service.min_evidence_score
    assert citations[0].chunk_id == "KB-SHIPPING-01:1:0000"
    assert citations[0].document_version == "1"
    assert citations[0].source_uri == "supportops://policy/shipping"


def test_unsupported_query_abstains_instead_of_hallucinating_evidence():
    service = KnowledgeService()
    assert service.search("explain quantum entanglement in detail") == []


def test_deterministic_grounded_answer_keeps_source_id():
    service = KnowledgeService()
    citations = service.search("怎么取消订阅自动续费？")
    answer = service.answer("怎么取消订阅自动续费？", citations)
    assert citations
    assert f"[{citations[0].document_id}]" in answer


def test_deterministic_embedding_is_stable_and_normalized():
    provider = DeterministicHashEmbedding(dimension=64)
    first = provider.embed(["refund policy"])[0]
    second = provider.embed(["refund policy"])[0]
    assert first == second
    assert len(first) == 64
    assert abs(sum(value * value for value in first) - 1.0) < 1e-9


def test_versioned_chunk_identity_and_content_hash_are_deterministic():
    source = KnowledgeSourceDocument(
        document_id="KB-TEST-01",
        version="7",
        title="Test policy",
        text="A deterministic support policy body.",
        source_uri="supportops://policy/test",
    )
    documents = build_retrieval_documents([source], chunker=KnowledgeChunker(max_chars=300))
    assert len(documents) == 1
    document = documents[0]
    assert document.index_id == "KB-TEST-01:7:0000"
    assert document.document_version == "7"
    assert document.content_hash == documents[0].content_hash

    changed = build_retrieval_documents(
        [
            KnowledgeSourceDocument(
                document_id="KB-TEST-01",
                version="7",
                title="Test policy",
                text="A changed support policy body.",
                source_uri="supportops://policy/test",
            )
        ],
        chunker=KnowledgeChunker(max_chars=300),
    )[0]
    assert changed.index_id == document.index_id
    assert changed.content_hash != document.content_hash


def test_dense_failure_degrades_to_sparse_and_is_visible():
    documents = default_knowledge_documents()

    class BrokenDense:
        def search(self, query: str, *, limit: int):
            del query, limit
            raise RetrievalBackendError(
                RetrievalFailureReason.QDRANT_UNAVAILABLE,
                operation="qdrant.search",
                message="simulated outage",
            )

    retriever = HybridRetriever(
        documents,
        dense_retriever=BrokenDense(),
        sparse_retriever=BM25Retriever(documents),
        allow_sparse_fallback=True,
    )
    service = KnowledgeService(documents=documents, retriever=retriever)
    result = service.search_with_status("refund policy")
    assert result.citations
    assert result.citations[0].document_id == "KB-REFUND-01"
    assert result.degraded is True
    assert result.degradation_reason == "qdrant_unavailable"


def test_dense_failure_is_not_swallowed_when_fallback_is_disabled():
    documents = default_knowledge_documents()

    class BrokenDense:
        def search(self, query: str, *, limit: int):
            del query, limit
            raise RetrievalBackendError(
                RetrievalFailureReason.QDRANT_UNAVAILABLE,
                operation="qdrant.search",
                message="simulated outage",
            )

    retriever = HybridRetriever(
        documents,
        dense_retriever=BrokenDense(),
        sparse_retriever=BM25Retriever(documents),
        allow_sparse_fallback=False,
    )
    with pytest.raises(RetrievalBackendError) as exc_info:
        retriever.search("refund policy")
    assert exc_info.value.reason is RetrievalFailureReason.QDRANT_UNAVAILABLE


def test_embedding_timeout_has_typed_failure(monkeypatch):
    request = httpx.Request("POST", "http://embedding.test/v1/embeddings")

    def timeout(*args, **kwargs):
        del args, kwargs
        raise httpx.ReadTimeout("simulated timeout", request=request)

    monkeypatch.setattr(httpx, "post", timeout)
    provider = OpenAICompatibleEmbedding(
        base_url="http://embedding.test/v1",
        model="mock-embedding",
        dimension=8,
        timeout_seconds=0.01,
    )
    with pytest.raises(RetrievalBackendError) as exc_info:
        provider.embed(["refund policy"])
    assert exc_info.value.reason is RetrievalFailureReason.EMBEDDING_TIMEOUT


def test_qdrant_backend_rejects_ci_only_embedding(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'supportops.db'}",
        knowledge_backend="qdrant",
        embedding_backend="deterministic",
        app_env="development",
        auth_mode="dev",
        mcp_auth_mode="disabled",
        seed_demo_data=True,
    )
    try:
        build_container(settings)
    except ValueError as exc:
        assert "requires EMBEDDING_BACKEND=openai" in str(exc)
    else:
        raise AssertionError("qdrant backend accepted deterministic CI embedding")


def test_qdrant_startup_failure_degrades_to_sparse_when_allowed(monkeypatch, tmp_path):
    def fail_qdrant_startup(*args, **kwargs):
        del args, kwargs
        raise RetrievalBackendError(
            RetrievalFailureReason.QDRANT_INDEX_ERROR,
            operation="qdrant.sync",
            message="simulated startup outage",
        )

    monkeypatch.setattr("app.container.QdrantDenseRetriever", fail_qdrant_startup)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'supportops.db'}",
        seed_demo_data=False,
        knowledge_backend="qdrant",
        embedding_backend="openai",
        embedding_base_url="http://embedding.test/v1",
        embedding_model="mock-embedding",
        embedding_dimension=64,
        retrieval_allow_sparse_fallback=True,
        app_env="development",
        auth_mode="dev",
        mcp_auth_mode="disabled",
    )

    container = build_container(settings)
    result = container.knowledge.search_with_status("refund policy")
    assert result.citations
    assert result.citations[0].document_id == "KB-REFUND-01"
    assert result.degraded is True
    assert result.degradation_reason == "qdrant_index_error"


def test_qdrant_startup_failure_fails_fast_when_fallback_disabled(monkeypatch, tmp_path):
    def fail_qdrant_startup(*args, **kwargs):
        del args, kwargs
        raise RetrievalBackendError(
            RetrievalFailureReason.QDRANT_INDEX_ERROR,
            operation="qdrant.sync",
            message="simulated startup outage",
        )

    monkeypatch.setattr("app.container.QdrantDenseRetriever", fail_qdrant_startup)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'supportops.db'}",
        seed_demo_data=False,
        knowledge_backend="qdrant",
        embedding_backend="openai",
        embedding_base_url="http://embedding.test/v1",
        embedding_model="mock-embedding",
        embedding_dimension=64,
        retrieval_allow_sparse_fallback=False,
        app_env="development",
        auth_mode="dev",
        mcp_auth_mode="disabled",
    )

    with pytest.raises(RetrievalBackendError) as exc_info:
        build_container(settings)
    assert exc_info.value.reason is RetrievalFailureReason.QDRANT_INDEX_ERROR
