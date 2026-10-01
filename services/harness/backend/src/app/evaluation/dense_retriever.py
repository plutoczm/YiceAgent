"""Evaluation-only exact Dense retrieval over the existing scoped PostgreSQL corpus."""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from numbers import Real
from pathlib import Path
from time import perf_counter
from typing import Protocol, Self
from uuid import UUID

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import text

from app.db.session import Database
from app.knowledge.models import KnowledgeModel
from app.retrieval.errors import InvalidSearchRequestError, RetrievalBackendError
from app.retrieval.models import (
    KnowledgeRetrievalContext,
    KnowledgeSearchRequest,
    RetrievalResult,
    RetrievedChunk,
)

DENSE_MODEL_ID = "BAAI/bge-small-en-v1.5"
DENSE_MODEL_DIMENSION = 384
FASTEMBED_VERSION = "0.8.1"
DENSE_RETRIEVER_ID = "dense-exact-cosine-v1"
MAX_DENSE_CORPUS_CHUNKS = 8192
FASTEMBED_HF_REPOSITORY = "Qdrant/bge-small-en-v1.5-onnx-Q"
FASTEMBED_MODEL_FILE = "model_optimized.onnx"

CURRENT_CORPUS_SQL = text("""
SELECT d.id AS document_id, r.id AS revision_id, c.id AS chunk_id,
       d.source_key, r.title, d.namespace, c.chunk_index, c.section_path,
       c.content, c.content_sha256, c.character_start, c.character_end
FROM knowledge_chunks AS c
JOIN knowledge_document_revisions AS r ON r.id = c.revision_id
JOIN knowledge_documents AS d ON d.id = r.document_id
WHERE d.tenant_id = :tenant_id AND d.namespace = :namespace
  AND NOT EXISTS (
      SELECT 1 FROM knowledge_document_revisions AS newer
      WHERE newer.document_id = d.id AND newer.revision_number > r.revision_number
  )
ORDER BY d.source_key COLLATE "C", c.chunk_index, c.section_path, c.content_sha256
LIMIT :max_chunks_plus_one
""")


class DenseCandidateError(Exception):
    """Safe evaluation-candidate failure without query, content or provider details."""

    def __init__(self) -> None:
        """Expose only a fixed message at the evaluation boundary."""
        super().__init__("Dense evaluation candidate failed closed.")


class EmbeddingProvider(Protocol):
    """Small application-owned seam for document and query embeddings."""

    @property
    def name(self) -> str:
        """Return a stable provider and model identity."""
        ...

    @property
    def dimension(self) -> int:
        """Return the exact vector dimension expected from this provider."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Embed document passages without returning provider-specific objects."""
        ...

    def embed_query(self, text: str) -> Sequence[float]:
        """Embed one query without returning provider-specific objects."""
        ...


class CurrentCorpusChunk(KnowledgeModel):
    """Detached current-revision chunk with the exact retrieval provenance fields."""

    document_id: UUID
    revision_id: UUID
    chunk_id: UUID
    source_key: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    namespace: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    chunk_index: int = Field(ge=0)
    section_path: tuple[str, ...]
    content: str = Field(min_length=1, max_length=16384, repr=False)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    character_start: int = Field(ge=0)
    character_end: int = Field(gt=0)

    @model_validator(mode="after")
    def source_range(self) -> Self:
        """Keep persisted provenance coordinates aligned with the detached chunk body."""
        if self.character_end - self.character_start != len(self.content):
            raise ValueError("Invalid retrieval provenance")
        return self


class CurrentKnowledgeCorpusReader:
    """Read only trusted tenant/namespace chunks from each document's latest revision."""

    def __init__(self, database: Database) -> None:
        """Borrow the same bounded database pool used by the lexical retriever."""
        self.database = database

    async def read(self, context: KnowledgeRetrievalContext) -> tuple[CurrentCorpusChunk, ...]:
        """Return current chunks in a stable bounded order, without global-vector filtering."""
        try:
            context = KnowledgeRetrievalContext.model_validate(context)
        except ValidationError:
            raise InvalidSearchRequestError() from None
        if self.database.sessions is None:
            raise RetrievalBackendError()
        try:
            async with self.database.sessions() as session:
                rows = (
                    (
                        await session.execute(
                            CURRENT_CORPUS_SQL,
                            {
                                "tenant_id": context.tenant_id,
                                "namespace": context.namespace,
                                "max_chunks_plus_one": MAX_DENSE_CORPUS_CHUNKS + 1,
                            },
                        )
                    )
                    .mappings()
                    .all()
                )
        except Exception:
            raise RetrievalBackendError() from None
        if len(rows) > MAX_DENSE_CORPUS_CHUNKS:
            raise DenseCandidateError()
        try:
            chunks = tuple(
                CurrentCorpusChunk(**{**dict(row), "section_path": tuple(row["section_path"])})
                for row in rows
            )
            stable_keys = [_stable_key(chunk) for chunk in chunks]
            if any(chunk.namespace != context.namespace for chunk in chunks):
                raise ValueError
            if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
                raise ValueError
            if len(set(stable_keys)) != len(stable_keys):
                raise ValueError
        except Exception:
            raise DenseCandidateError() from None
        return chunks


def _stable_key(
    chunk: CurrentCorpusChunk,
) -> tuple[str, int, tuple[str, ...], str]:
    """Use logical provenance rather than recreation-specific UUIDs for tie ordering."""
    return (chunk.source_key, chunk.chunk_index, chunk.section_path, chunk.content_sha256)


def _normalize_vector(values: Iterable[float], dimension: int) -> tuple[float, ...]:
    """Validate a vector and explicitly L2-normalize it, failing closed on bad output."""
    if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension <= 0:
        raise DenseCandidateError()
    try:
        raw_vector = tuple(values)
    except (TypeError, ValueError, OverflowError):
        raise DenseCandidateError() from None
    if any(isinstance(value, bool) or not isinstance(value, Real) for value in raw_vector):
        raise DenseCandidateError()
    try:
        vector = tuple(float(value) for value in raw_vector)
    except (TypeError, ValueError, OverflowError):
        raise DenseCandidateError() from None
    if len(vector) != dimension or not vector or any(not math.isfinite(value) for value in vector):
        raise DenseCandidateError()
    norm = math.sqrt(math.fsum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise DenseCandidateError()
    normalized = tuple(value / norm for value in vector)
    if any(not math.isfinite(value) for value in normalized):
        raise DenseCandidateError()
    return normalized


def _fingerprint_chunks(chunks: Sequence[CurrentCorpusChunk]) -> str:
    """Hash stable logical chunk provenance and content hashes, excluding database UUIDs."""
    payload = [
        {
            "source_key": chunk.source_key,
            "chunk_index": chunk.chunk_index,
            "section_path": chunk.section_path,
            "content_sha256": chunk.content_sha256,
        }
        for chunk in sorted(chunks, key=_stable_key)
    ]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _IndexedChunk:
    """One trusted corpus chunk paired with a validated unit vector."""

    chunk: CurrentCorpusChunk
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class DenseCorpusIndex:
    """Immutable exact in-memory vectors bound to one trusted tenant and namespace."""

    tenant_id: UUID
    namespace: str
    dimension: int
    chunks: tuple[_IndexedChunk, ...]
    corpus_sha256: str


@dataclass(frozen=True, slots=True)
class DenseIndexBuild:
    """Build result with inference lifecycle timings separated from query latency."""

    retriever: DenseEvaluationRetriever
    model_load_ms: float
    corpus_embedding_ms: float
    index_build_ms: float
    chunk_count: int


class DenseEvaluationRetriever:
    """KnowledgeRetriever-compatible exact cosine candidate, isolated from production wiring."""

    def __init__(self, index: DenseCorpusIndex, embeddings: EmbeddingProvider) -> None:
        """Bind a prebuilt index and its provider; no database or model I/O occurs here."""
        self.index = index
        self.embeddings = embeddings

    @property
    def name(self) -> str:
        """Return a versioned exact-search identity including the frozen embedding provider."""
        return f"{DENSE_RETRIEVER_ID}:{self.embeddings.name}"

    @classmethod
    async def build(
        cls,
        database: Database,
        context: KnowledgeRetrievalContext,
        embeddings: EmbeddingProvider,
        *,
        reader: CurrentKnowledgeCorpusReader | None = None,
    ) -> DenseIndexBuild:
        """Read one scoped current corpus, embed once, and create an exact in-memory index."""
        try:
            context = KnowledgeRetrievalContext.model_validate(context)
            dimension = embeddings.dimension
            if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension <= 0:
                raise DenseCandidateError()
        except (ValidationError, AttributeError, TypeError, ValueError):
            raise DenseCandidateError() from None
        chunks = await (reader or CurrentKnowledgeCorpusReader(database)).read(context)
        if not chunks:
            raise DenseCandidateError()
        if (
            any(chunk.namespace != context.namespace for chunk in chunks)
            or len({chunk.chunk_id for chunk in chunks}) != len(chunks)
            or len({_stable_key(chunk) for chunk in chunks}) != len(chunks)
        ):
            raise DenseCandidateError()

        model_load_started = perf_counter()
        prepare = getattr(embeddings, "prepare", None)
        if callable(prepare):
            try:
                prepare()
            except Exception:
                raise DenseCandidateError() from None
        model_load_ms = (perf_counter() - model_load_started) * 1000

        embedding_started = perf_counter()
        try:
            vectors = tuple(embeddings.embed_documents(tuple(chunk.content for chunk in chunks)))
        except Exception:
            raise DenseCandidateError() from None
        corpus_embedding_ms = (perf_counter() - embedding_started) * 1000
        if len(vectors) != len(chunks):
            raise DenseCandidateError()

        index_started = perf_counter()
        normalized = tuple(_normalize_vector(vector, dimension) for vector in vectors)
        entries = tuple(
            _IndexedChunk(chunk=chunk, vector=vector)
            for chunk, vector in zip(chunks, normalized, strict=True)
        )
        index = DenseCorpusIndex(
            tenant_id=context.tenant_id,
            namespace=context.namespace,
            dimension=dimension,
            chunks=entries,
            corpus_sha256=_fingerprint_chunks(chunks),
        )
        retriever = cls(index, embeddings)
        index_build_ms = (perf_counter() - index_started) * 1000
        return DenseIndexBuild(
            retriever=retriever,
            model_load_ms=model_load_ms,
            corpus_embedding_ms=corpus_embedding_ms,
            index_build_ms=index_build_ms,
            chunk_count=len(chunks),
        )

    async def search(
        self,
        context: KnowledgeRetrievalContext,
        request: KnowledgeSearchRequest,
    ) -> RetrievalResult:
        """Embed one query, rank all scoped chunks by exact cosine and return exact top-k."""
        started = perf_counter()
        try:
            context = KnowledgeRetrievalContext.model_validate(context)
            request = KnowledgeSearchRequest.model_validate(request)
        except ValidationError:
            raise InvalidSearchRequestError() from None
        if context.tenant_id != self.index.tenant_id or context.namespace != self.index.namespace:
            raise InvalidSearchRequestError()
        try:
            dimension = self.embeddings.dimension
        except Exception:
            raise DenseCandidateError() from None
        if dimension != self.index.dimension:
            raise DenseCandidateError()
        try:
            query_vector = _normalize_vector(
                self.embeddings.embed_query(request.query), self.index.dimension
            )
        except DenseCandidateError:
            raise
        except Exception:
            raise DenseCandidateError() from None

        scored = [
            (
                math.fsum(
                    left * right for left, right in zip(query_vector, entry.vector, strict=True)
                ),
                _stable_key(entry.chunk),
                entry.chunk,
            )
            for entry in self.index.chunks
        ]
        if any(not math.isfinite(score) for score, _, _ in scored):
            raise DenseCandidateError()
        ranked = sorted(scored, key=lambda item: (-item[0], item[1]))[: request.top_k]
        try:
            chunks = tuple(
                RetrievedChunk(
                    document_id=item.document_id,
                    revision_id=item.revision_id,
                    chunk_id=item.chunk_id,
                    source_key=item.source_key,
                    title=item.title,
                    namespace=item.namespace,
                    chunk_index=item.chunk_index,
                    section_path=item.section_path,
                    content=item.content,
                    content_sha256=item.content_sha256,
                    character_start=item.character_start,
                    character_end=item.character_end,
                    score=score,
                    rank=rank,
                )
                for rank, (score, _, item) in enumerate(ranked, 1)
            )
            return RetrievalResult(
                retriever=self.name,
                namespace=context.namespace,
                chunks=chunks,
                duration_ms=(perf_counter() - started) * 1000,
            )
        except Exception:
            raise DenseCandidateError() from None


class FastEmbedEmbeddingProvider:
    """Lazy FastEmbed adapter pinned to local-only BGE-small English inference."""

    name = f"fastembed-{FASTEMBED_VERSION}:{DENSE_MODEL_ID}"
    dimension = DENSE_MODEL_DIMENSION
    model_id = DENSE_MODEL_ID
    library_version = FASTEMBED_VERSION

    def __init__(self, cache_dir: Path | None = None) -> None:
        """Store an optional cache path without creating directories or importing FastEmbed."""
        self._cache_dir = cache_dir
        self._model: object | None = None

    @property
    def artifact_revision(self) -> str | None:
        """Return the cached model snapshot revision when a complete local artifact exists."""
        root = self.resolved_cache_dir(self._cache_dir)
        repository = root / f"models--{FASTEMBED_HF_REPOSITORY.replace('/', '--')}"
        snapshots = repository / "snapshots"
        try:
            revision = (repository / "refs" / "main").read_text(encoding="utf-8").strip()
            snapshot = snapshots / revision
            complete = all(
                (snapshot / filename).is_file()
                for filename in (FASTEMBED_MODEL_FILE, "tokenizer.json", "config.json")
            )
        except OSError:
            return None
        return revision if complete else None

    @classmethod
    def resolved_cache_dir(cls, cache_dir: Path | None = None) -> Path:
        """Resolve FastEmbed's documented cache location without creating it."""
        if cache_dir is not None:
            return cache_dir
        configured = os.environ.get("FASTEMBED_CACHE_PATH")
        return Path(configured) if configured else Path(tempfile.gettempdir()) / "fastembed_cache"

    @classmethod
    def model_available_local(cls, cache_dir: Path | None = None) -> bool:
        """Check expected local ONNX/tokenizer files only; never invoke model discovery."""
        root = cls.resolved_cache_dir(cache_dir)
        model_root = root / f"models--{FASTEMBED_HF_REPOSITORY.replace('/', '--')}" / "snapshots"
        try:
            for snapshot in model_root.iterdir():
                if not snapshot.is_dir():
                    continue
                if all(
                    (snapshot / filename).is_file()
                    for filename in (FASTEMBED_MODEL_FILE, "tokenizer.json", "config.json")
                ):
                    return True
        except OSError:
            return False
        return False

    def prepare(self) -> None:
        """Load only an already-cached model, with FastEmbed network fallback disabled."""
        if self._model is not None:
            return
        if not self.model_available_local(self._cache_dir):
            raise DenseCandidateError()
        try:
            from fastembed import TextEmbedding  # type: ignore[import-not-found]

            kwargs: dict[str, object] = {
                "model_name": DENSE_MODEL_ID,
                "local_files_only": True,
            }
            if self._cache_dir is not None:
                kwargs["cache_dir"] = str(self._cache_dir)
            self._model = TextEmbedding(**kwargs)
            if getattr(self._model, "embedding_size", None) != DENSE_MODEL_DIMENSION:
                self._model = None
                raise DenseCandidateError()
        except DenseCandidateError:
            raise
        except Exception:
            raise DenseCandidateError() from None

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Return passage vectors from the local-only FastEmbed API."""
        self.prepare()
        try:
            model = self._model
            passage_embed = getattr(model, "passage_embed", None)
            if not callable(passage_embed):
                raise DenseCandidateError()
            values = passage_embed(texts)
            return tuple(tuple(float(value) for value in vector) for vector in values)
        except DenseCandidateError:
            raise
        except Exception:
            raise DenseCandidateError() from None

    def embed_query(self, text_value: str) -> Sequence[float]:
        """Return one query vector from the local-only FastEmbed API."""
        self.prepare()
        try:
            model = self._model
            query_embed = getattr(model, "query_embed", None)
            if not callable(query_embed):
                raise DenseCandidateError()
            values = tuple(query_embed([text_value]))
            if len(values) != 1:
                raise DenseCandidateError()
            return tuple(float(value) for value in values[0])
        except DenseCandidateError:
            raise
        except Exception:
            raise DenseCandidateError() from None
