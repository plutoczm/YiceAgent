import hashlib
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.db.session import Database
from app.evaluation.dense_retriever import (
    CurrentCorpusChunk,
    CurrentKnowledgeCorpusReader,
    DenseCandidateError,
    DenseEvaluationRetriever,
    FastEmbedEmbeddingProvider,
)
from app.retrieval.errors import InvalidSearchRequestError
from app.retrieval.models import KnowledgeRetrievalContext, KnowledgeSearchRequest
from app.retrieval.protocols import KnowledgeRetriever


class FixedCorpusReader(CurrentKnowledgeCorpusReader):
    def __init__(self, chunks: tuple[CurrentCorpusChunk, ...]) -> None:
        self.chunks = chunks

    async def read(self, context: KnowledgeRetrievalContext) -> tuple[CurrentCorpusChunk, ...]:
        assert context.namespace in {chunk.namespace for chunk in self.chunks}
        return self.chunks


class FakeEmbeddingProvider:
    """Test-only stable text mapping; deliberately does not use Python hash()."""

    name = "fake-fixed-v1"
    dimension = 2

    def __init__(
        self,
        document_vectors: Mapping[str, Sequence[float]],
        query_vector: Sequence[float],
    ) -> None:
        self.document_vectors = document_vectors
        self.query_vector = query_vector
        self.query_calls = 0

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return tuple(self.document_vectors[text] for text in texts)

    def embed_query(self, text: str) -> Sequence[float]:
        assert text == "fixed query"
        self.query_calls += 1
        return self.query_vector


def corpus_chunk(source_key: str, content: str, *, chunk_index: int = 0) -> CurrentCorpusChunk:
    return CurrentCorpusChunk(
        document_id=uuid4(),
        revision_id=uuid4(),
        chunk_id=uuid4(),
        source_key=source_key,
        title=f"Title {source_key}",
        namespace="supportops",
        chunk_index=chunk_index,
        section_path=("Policy",),
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        character_start=0,
        character_end=len(content),
    )


def test_corpus_chunk_rejects_invalid_source_coordinates() -> None:
    chunk = corpus_chunk("policy.md", "content")
    with pytest.raises(ValueError):
        CurrentCorpusChunk.model_validate({**chunk.model_dump(), "character_end": 99})


@pytest.mark.anyio
async def test_exact_cosine_topk_provenance_and_protocol() -> None:
    chunks = (
        corpus_chunk("alpha.md", "alpha evidence"),
        corpus_chunk("beta.md", "beta evidence"),
        corpus_chunk("gamma.md", "gamma evidence"),
    )
    fake = FakeEmbeddingProvider(
        {
            "alpha evidence": (1.0, 0.0),
            "beta evidence": (0.8, 0.6),
            "gamma evidence": (0.0, 1.0),
        },
        (1.0, 0.0),
    )
    context = KnowledgeRetrievalContext(tenant_id=uuid4(), namespace="supportops")
    database = Database(Settings(_env_file=None))
    built = await DenseEvaluationRetriever.build(
        database, context, fake, reader=FixedCorpusReader(chunks)
    )
    retriever: KnowledgeRetriever = built.retriever

    top_two = await retriever.search(context, KnowledgeSearchRequest(query="fixed query", top_k=2))
    assert [chunk.source_key for chunk in top_two.chunks] == ["alpha.md", "beta.md"]
    assert [chunk.rank for chunk in top_two.chunks] == [1, 2]
    assert [chunk.score for chunk in top_two.chunks] == pytest.approx([1.0, 0.8])
    assert top_two.chunks[0].content == "alpha evidence"
    assert top_two.chunks[0].section_path == ("Policy",)
    assert top_two.chunks[0].content_sha256 == chunks[0].content_sha256

    top_one = await retriever.search(context, KnowledgeSearchRequest(query="fixed query", top_k=1))
    assert [chunk.source_key for chunk in top_one.chunks] == ["alpha.md"]

    all_results = await retriever.search(
        context, KnowledgeSearchRequest(query="fixed query", top_k=5)
    )
    assert len(all_results.chunks) == 3
    assert built.chunk_count == 3
    assert built.model_load_ms >= 0
    assert built.corpus_embedding_ms >= 0
    assert built.index_build_ms >= 0


@pytest.mark.anyio
async def test_equal_scores_use_stable_provenance_not_uuid_order() -> None:
    chunks = (corpus_chunk("zeta.md", "zeta"), corpus_chunk("alpha.md", "alpha"))
    vectors = {"zeta": (1.0, 0.0), "alpha": (1.0, 0.0)}
    context = KnowledgeRetrievalContext(tenant_id=uuid4(), namespace="supportops")
    results = []
    for order in (chunks, tuple(reversed(chunks))):
        built = await DenseEvaluationRetriever.build(
            Database(Settings(_env_file=None)),
            context,
            FakeEmbeddingProvider(vectors, (1.0, 0.0)),
            reader=FixedCorpusReader(order),
        )
        result = await built.retriever.search(
            context, KnowledgeSearchRequest(query="fixed query", top_k=2)
        )
        results.append([chunk.source_key for chunk in result.chunks])
    assert results == [["alpha.md", "zeta.md"], ["alpha.md", "zeta.md"]]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_vector",
    [(), (1.0,), (math.nan, 0.0), (math.inf, 0.0), (-math.inf, 0.0), (0.0, 0.0)],
)
async def test_invalid_document_vectors_fail_closed(bad_vector: tuple[float, ...]) -> None:
    chunk = corpus_chunk("alpha.md", "alpha evidence")
    fake = FakeEmbeddingProvider({"alpha evidence": bad_vector}, (1.0, 0.0))
    with pytest.raises(DenseCandidateError):
        await DenseEvaluationRetriever.build(
            Database(Settings(_env_file=None)),
            KnowledgeRetrievalContext(tenant_id=uuid4(), namespace="supportops"),
            fake,
            reader=FixedCorpusReader((chunk,)),
        )


@pytest.mark.anyio
async def test_invalid_query_vector_and_vector_count_fail_closed() -> None:
    chunk = corpus_chunk("alpha.md", "alpha evidence")
    context = KnowledgeRetrievalContext(tenant_id=uuid4(), namespace="supportops")
    fake = FakeEmbeddingProvider({"alpha evidence": (1.0, 0.0)}, (math.nan, 1.0))
    built = await DenseEvaluationRetriever.build(
        Database(Settings(_env_file=None)), context, fake, reader=FixedCorpusReader((chunk,))
    )
    with pytest.raises(DenseCandidateError):
        await built.retriever.search(context, KnowledgeSearchRequest(query="fixed query"))

    class WrongCountProvider(FakeEmbeddingProvider):
        def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
            return ()

    with pytest.raises(DenseCandidateError):
        await DenseEvaluationRetriever.build(
            Database(Settings(_env_file=None)),
            context,
            WrongCountProvider({"alpha evidence": (1.0, 0.0)}, (1.0, 0.0)),
            reader=FixedCorpusReader((chunk,)),
        )


@pytest.mark.anyio
async def test_scope_mismatch_is_rejected_before_query_embedding() -> None:
    chunk = corpus_chunk("alpha.md", "alpha evidence")
    context = KnowledgeRetrievalContext(tenant_id=uuid4(), namespace="supportops")
    fake = FakeEmbeddingProvider({"alpha evidence": (1.0, 0.0)}, (1.0, 0.0))
    built = await DenseEvaluationRetriever.build(
        Database(Settings(_env_file=None)), context, fake, reader=FixedCorpusReader((chunk,))
    )
    for wrong in (
        KnowledgeRetrievalContext(tenant_id=uuid4(), namespace="supportops"),
        KnowledgeRetrievalContext(tenant_id=context.tenant_id, namespace="datacopilot"),
    ):
        with pytest.raises(InvalidSearchRequestError):
            await built.retriever.search(wrong, KnowledgeSearchRequest(query="fixed query"))
    assert fake.query_calls == 0


@pytest.mark.anyio
async def test_duplicate_chunk_identity_fails_closed() -> None:
    chunk = corpus_chunk("alpha.md", "alpha evidence")
    duplicate = chunk.model_copy(update={"chunk_id": uuid4()})
    with pytest.raises(DenseCandidateError):
        await DenseEvaluationRetriever.build(
            Database(Settings(_env_file=None)),
            KnowledgeRetrievalContext(tenant_id=uuid4(), namespace="supportops"),
            FakeEmbeddingProvider({"alpha evidence": (1.0, 0.0)}, (1.0, 0.0)),
            reader=FixedCorpusReader((chunk, duplicate)),
        )


def test_fastembed_adapter_preflight_is_lazy_and_read_only() -> None:
    path = Path(".artifacts/.phase7c-a-plan/not-a-model-cache")
    provider = FastEmbedEmbeddingProvider(cache_dir=path)
    assert provider._model is None
    assert provider.artifact_revision is None
    assert not provider.model_available_local(path)
    with pytest.raises(DenseCandidateError):
        provider.prepare()
    assert not path.exists()


def test_fastembed_adapter_forces_local_files_only(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    class FakeTextEmbedding:
        def __init__(self, **kwargs: object) -> None:
            observed.update(kwargs)
            self.embedding_size = 384

    fake_module = ModuleType("fastembed")
    fake_module.TextEmbedding = FakeTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)
    monkeypatch.setattr(
        FastEmbedEmbeddingProvider,
        "model_available_local",
        classmethod(lambda cls, cache_dir=None: True),
    )

    provider = FastEmbedEmbeddingProvider()
    provider.prepare()
    assert observed == {
        "model_name": "BAAI/bge-small-en-v1.5",
        "local_files_only": True,
    }
