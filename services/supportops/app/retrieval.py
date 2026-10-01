from __future__ import annotations

import hashlib
import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import httpx


@dataclass(frozen=True, slots=True)
class RetrievalDocument:
    document_id: str
    title: str
    text: str
    source_uri: str = "builtin://supportops/knowledge"
    document_version: str = "1"
    chunk_id: str | None = None

    @property
    def searchable_text(self) -> str:
        return f"{self.title} {self.text}"

    @property
    def index_id(self) -> str:
        return self.chunk_id or self.document_id

    @property
    def content_hash(self) -> str:
        material = "\x00".join(
            (
                self.document_id,
                self.document_version,
                self.source_uri,
                self.title,
                self.text,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    document: RetrievalDocument
    fused_score: float
    rerank_score: float
    dense_score: float
    sparse_score: float
    evidence_score: float
    degraded: bool = False
    degradation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalSearchResult:
    hits: list[RetrievalHit]
    degraded: bool = False
    degradation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class IndexSyncStats:
    desired_chunks: int
    embedded_chunks: int
    upserted_chunks: int
    deleted_chunks: int
    unchanged_chunks: int


class RetrievalFailureReason(StrEnum):
    EMBEDDING_TIMEOUT = "embedding_timeout"
    EMBEDDING_RATE_LIMITED = "embedding_rate_limited"
    EMBEDDING_PROVIDER_ERROR = "embedding_provider_error"
    EMBEDDING_INVALID_RESPONSE = "embedding_invalid_response"
    QDRANT_UNAVAILABLE = "qdrant_unavailable"
    QDRANT_INDEX_ERROR = "qdrant_index_error"


class RetrievalBackendError(RuntimeError):
    def __init__(self, reason: RetrievalFailureReason, *, operation: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.operation = operation


class EmbeddingProvider(Protocol):
    @property
    def dimension(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class DenseRetriever(Protocol):
    def search(self, query: str, *, limit: int) -> list[tuple[RetrievalDocument, float]]: ...


_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "do",
    "does",
    "for",
    "how",
    "i",
    "in",
    "is",
    "it",
    "my",
    "of",
    "on",
    "the",
    "to",
    "what",
    "when",
    "where",
    "why",
    "with",
    "you",
}


def tokenize(text: str) -> list[str]:
    normalized = text.lower()
    english = [
        _canonicalize_english(token)
        for token in re.findall(r"[a-z0-9_-]+", normalized)
        if len(token) > 1 and token not in _STOPWORDS
    ]
    chinese_tokens: list[str] = []
    for segment in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(segment) == 1:
            chinese_tokens.append(segment)
            continue
        chinese_tokens.extend(segment[index : index + 2] for index in range(len(segment) - 1))
        if len(segment) <= 8:
            chinese_tokens.append(segment)
    return english + chinese_tokens


def _canonicalize_english(token: str) -> str:
    """Normalize common inflections without adding a heavyweight NLP runtime."""
    for suffix in ("ations", "ation", "ments", "ment", "ingly", "edly", "ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            base = token[: -len(suffix)]
            if len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
            return base
    return token


class DeterministicHashEmbedding:
    """Deterministic vector fallback for CI/local development, not a semantic model."""

    def __init__(self, dimension: int = 192) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            first = int.from_bytes(digest[:8], "big") % self.dimension
            second = int.from_bytes(digest[8:], "big") % self.dimension
            vector[first] += 1.0
            vector[second] += 0.5
        return _normalize(vector)


class OpenAICompatibleEmbedding:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        dimension: int | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self._dimension = dimension
        self.timeout_seconds = timeout_seconds

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise RuntimeError("embedding dimension is unknown before the first embedding call")
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = httpx.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json={"model": self.model, "input": texts},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise RetrievalBackendError(
                RetrievalFailureReason.EMBEDDING_TIMEOUT,
                operation="embedding.embed",
                message="embedding provider timed out",
            ) from exc
        except httpx.HTTPStatusError as exc:
            reason = (
                RetrievalFailureReason.EMBEDDING_RATE_LIMITED
                if exc.response.status_code == 429
                else RetrievalFailureReason.EMBEDDING_PROVIDER_ERROR
            )
            raise RetrievalBackendError(
                reason,
                operation="embedding.embed",
                message=f"embedding provider returned HTTP {exc.response.status_code}",
            ) from exc
        except httpx.RequestError as exc:
            raise RetrievalBackendError(
                RetrievalFailureReason.EMBEDDING_PROVIDER_ERROR,
                operation="embedding.embed",
                message="embedding provider request failed",
            ) from exc

        try:
            payload = response.json()
            ordered = sorted(payload["data"], key=lambda item: int(item["index"]))
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise RetrievalBackendError(
                RetrievalFailureReason.EMBEDDING_INVALID_RESPONSE,
                operation="embedding.parse",
                message="embedding provider returned an invalid response",
            ) from exc

        if len(vectors) != len(texts):
            raise RetrievalBackendError(
                RetrievalFailureReason.EMBEDDING_INVALID_RESPONSE,
                operation="embedding.parse",
                message="embedding provider returned an unexpected vector count",
            )
        if vectors:
            dimension = len(vectors[0])
            if not dimension or any(len(vector) != dimension for vector in vectors):
                raise RetrievalBackendError(
                    RetrievalFailureReason.EMBEDDING_INVALID_RESPONSE,
                    operation="embedding.parse",
                    message="embedding provider returned inconsistent dimensions",
                )
            if self._dimension is not None and self._dimension != dimension:
                raise RetrievalBackendError(
                    RetrievalFailureReason.EMBEDDING_INVALID_RESPONSE,
                    operation="embedding.parse",
                    message="embedding provider dimension changed",
                )
            self._dimension = dimension
        return [_normalize(vector) for vector in vectors]


class BM25Retriever:
    def __init__(
        self,
        documents: list[RetrievalDocument],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.documents = documents
        self.k1 = k1
        self.b = b
        self._tokens = [tokenize(document.searchable_text) for document in documents]
        self._lengths = [len(tokens) for tokens in self._tokens]
        self._avg_length = sum(self._lengths) / max(len(self._lengths), 1)
        self._document_frequency = Counter(
            token for tokens in self._tokens for token in set(tokens)
        )

    def search(self, query: str, *, limit: int) -> list[tuple[RetrievalDocument, float]]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        scores: list[tuple[RetrievalDocument, float]] = []
        document_count = len(self.documents)
        for document, tokens, length in zip(
            self.documents, self._tokens, self._lengths, strict=True
        ):
            frequencies = Counter(tokens)
            score = 0.0
            for token in query_tokens:
                frequency = frequencies[token]
                if not frequency:
                    continue
                df = self._document_frequency[token]
                idf = math.log(1.0 + (document_count - df + 0.5) / (df + 0.5))
                denominator = frequency + self.k1 * (
                    1.0 - self.b + self.b * length / max(self._avg_length, 1.0)
                )
                score += idf * (frequency * (self.k1 + 1.0)) / denominator
            if score > 0:
                scores.append((document, score))
        return sorted(scores, key=lambda item: item[1], reverse=True)[:limit]


class InMemoryDenseRetriever:
    def __init__(
        self,
        documents: list[RetrievalDocument],
        provider: EmbeddingProvider,
    ) -> None:
        self.documents = documents
        self.provider = provider
        self._vectors = provider.embed([document.searchable_text for document in documents])

    def search(self, query: str, *, limit: int) -> list[tuple[RetrievalDocument, float]]:
        query_vector = self.provider.embed([query])[0]
        scored = [
            (document, max(0.0, _dot(query_vector, vector)))
            for document, vector in zip(self.documents, self._vectors, strict=True)
        ]
        return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]


class UnavailableDenseRetriever:
    """Keeps the sparse path available after a typed dense-backend startup failure."""

    def __init__(self, error: RetrievalBackendError) -> None:
        self.error = error

    def search(self, query: str, *, limit: int) -> list[tuple[RetrievalDocument, float]]:
        del query, limit
        raise self.error


class QdrantDenseRetriever:
    """Dense adapter with content-addressed incremental synchronization."""

    def __init__(
        self,
        documents: list[RetrievalDocument],
        provider: EmbeddingProvider,
        *,
        url: str,
        collection: str,
        api_key: str | None = None,
        timeout_seconds: float = 5.0,
        sync_on_start: bool = True,
    ) -> None:
        try:
            from qdrant_client import QdrantClient, models
        except ImportError as exc:
            raise RuntimeError("Qdrant support requires the 'rag' optional dependency") from exc

        self.documents = {document.index_id: document for document in documents}
        self.provider = provider
        self.collection = collection
        self.models = models
        self.client = QdrantClient(url=url, api_key=api_key, timeout=timeout_seconds)
        self.sync_stats: IndexSyncStats | None = None
        if sync_on_start:
            self.sync_stats = self.sync_documents(list(documents))

    def sync_documents(self, documents: list[RetrievalDocument]) -> IndexSyncStats:
        desired = {document.index_id: document for document in documents}
        try:
            collection_exists = self.client.collection_exists(self.collection)
            if not collection_exists:
                vectors = self.provider.embed(
                    [document.searchable_text for document in desired.values()]
                )
                dimension = len(vectors[0]) if vectors else self.provider.dimension
                self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config={
                        "dense": self.models.VectorParams(
                            size=dimension,
                            distance=self.models.Distance.COSINE,
                        )
                    },
                )
                self._upsert_documents(list(desired.values()), vectors)
                self.documents = desired
                return IndexSyncStats(
                    desired_chunks=len(desired),
                    embedded_chunks=len(desired),
                    upserted_chunks=len(desired),
                    deleted_chunks=0,
                    unchanged_chunks=0,
                )

            remote = self._remote_index_state()
            changed = [
                document
                for index_id, document in desired.items()
                if remote.get(index_id, {}).get("content_hash") != document.content_hash
            ]
            unchanged = len(desired) - len(changed)
            if changed:
                vectors = self.provider.embed([document.searchable_text for document in changed])
                self._upsert_documents(changed, vectors)

            stale_point_ids = [
                state["point_id"] for index_id, state in remote.items() if index_id not in desired
            ]
            if stale_point_ids:
                self.client.delete(
                    collection_name=self.collection,
                    points_selector=self.models.PointIdsList(points=stale_point_ids),
                    wait=True,
                )
            self.documents = desired
            return IndexSyncStats(
                desired_chunks=len(desired),
                embedded_chunks=len(changed),
                upserted_chunks=len(changed),
                deleted_chunks=len(stale_point_ids),
                unchanged_chunks=unchanged,
            )
        except RetrievalBackendError:
            raise
        except Exception as exc:
            raise RetrievalBackendError(
                RetrievalFailureReason.QDRANT_INDEX_ERROR,
                operation="qdrant.sync",
                message="Qdrant index synchronization failed",
            ) from exc

    def _remote_index_state(self) -> dict[str, dict[str, Any]]:
        state: dict[str, dict[str, Any]] = {}
        offset: Any = None
        while True:
            records, offset = self.client.scroll(
                collection_name=self.collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for record in records:
                payload = record.payload or {}
                index_id = str(payload.get("index_id", ""))
                if not index_id:
                    continue
                state[index_id] = {
                    "point_id": record.id,
                    "content_hash": str(payload.get("content_hash", "")),
                }
            if offset is None:
                break
        return state

    def _upsert_documents(
        self,
        documents: list[RetrievalDocument],
        vectors: list[list[float]],
    ) -> None:
        points = [
            self.models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, document.index_id)),
                vector={"dense": vector},
                payload={
                    "index_id": document.index_id,
                    "document_id": document.document_id,
                    "chunk_id": document.chunk_id,
                    "document_version": document.document_version,
                    "source_uri": document.source_uri,
                    "content_hash": document.content_hash,
                },
            )
            for document, vector in zip(documents, vectors, strict=True)
        ]
        if points:
            self.client.upsert(collection_name=self.collection, points=points, wait=True)

    def search(self, query: str, *, limit: int) -> list[tuple[RetrievalDocument, float]]:
        vector = self.provider.embed([query])[0]
        try:
            response = self.client.query_points(
                collection_name=self.collection,
                query=vector,
                using="dense",
                limit=limit,
                with_payload=True,
            )
        except Exception as exc:
            raise RetrievalBackendError(
                RetrievalFailureReason.QDRANT_UNAVAILABLE,
                operation="qdrant.search",
                message="Qdrant dense search failed",
            ) from exc

        results: list[tuple[RetrievalDocument, float]] = []
        for point in response.points:
            payload = point.payload or {}
            index_id = str(payload.get("index_id", ""))
            document = self.documents.get(index_id)
            if document is not None:
                results.append((document, max(0.0, float(point.score))))
        return results


class HybridRetriever:
    def __init__(
        self,
        documents: list[RetrievalDocument],
        *,
        dense_retriever: DenseRetriever,
        sparse_retriever: BM25Retriever | None = None,
        rrf_k: int = 60,
        dense_weight: float = 1.0,
        sparse_weight: float = 1.5,
        allow_sparse_fallback: bool = True,
    ) -> None:
        if dense_weight <= 0 or sparse_weight <= 0:
            raise ValueError("RRF weights must be positive")
        self.documents = documents
        self.dense = dense_retriever
        self.sparse = sparse_retriever or BM25Retriever(documents)
        self.rrf_k = rrf_k
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight
        self.allow_sparse_fallback = allow_sparse_fallback

    def search(self, query: str, *, limit: int = 3, prefetch: int = 10) -> list[RetrievalHit]:
        return self.search_with_status(query, limit=limit, prefetch=prefetch).hits

    def search_with_status(
        self,
        query: str,
        *,
        limit: int = 3,
        prefetch: int = 10,
    ) -> RetrievalSearchResult:
        degraded = False
        degradation_reason: str | None = None
        try:
            dense = self.dense.search(query, limit=prefetch)
        except RetrievalBackendError as exc:
            if not self.allow_sparse_fallback:
                raise
            dense = []
            degraded = True
            degradation_reason = exc.reason.value

        sparse = self.sparse.search(query, limit=prefetch)
        dense_rank = {document.index_id: rank for rank, (document, _) in enumerate(dense, 1)}
        sparse_rank = {document.index_id: rank for rank, (document, _) in enumerate(sparse, 1)}
        dense_score = {document.index_id: score for document, score in dense}
        sparse_score = {document.index_id: score for document, score in sparse}

        candidates = set(dense_rank) | set(sparse_rank)
        query_tokens = set(tokenize(query))
        by_id = {document.index_id: document for document in self.documents}
        fused_scores: dict[str, float] = {}
        for index_id in candidates:
            fused = 0.0
            if index_id in dense_rank:
                fused += self.dense_weight / (self.rrf_k + dense_rank[index_id])
            if index_id in sparse_rank:
                fused += self.sparse_weight / (self.rrf_k + sparse_rank[index_id])
            fused_scores[index_id] = fused

        max_dense = max(dense_score.values(), default=1.0) or 1.0
        max_sparse = max(sparse_score.values(), default=1.0) or 1.0
        max_fused = max(fused_scores.values(), default=1.0) or 1.0
        hits: list[RetrievalHit] = []
        for index_id in candidates:
            document = by_id[index_id]
            sparse_raw = sparse_score.get(index_id, 0.0)
            dense_raw = dense_score.get(index_id, 0.0)
            fused = fused_scores[index_id]
            document_tokens = set(tokenize(document.searchable_text))
            title_tokens = set(tokenize(document.title))
            coverage = len(query_tokens & document_tokens) / max(len(query_tokens), 1)
            title_coverage = len(query_tokens & title_tokens) / max(len(query_tokens), 1)
            sparse_normalized = sparse_raw / (1.0 + sparse_raw)
            evidence = min(
                1.0,
                0.45 * dense_raw + 0.35 * sparse_normalized + 0.20 * coverage,
            )
            rerank = (
                0.45 * sparse_raw / max_sparse
                + 0.25 * dense_raw / max_dense
                + 0.20 * title_coverage
                + 0.10 * fused / max_fused
            )
            hits.append(
                RetrievalHit(
                    document=document,
                    fused_score=round(fused, 6),
                    rerank_score=round(rerank, 6),
                    dense_score=round(dense_raw, 6),
                    sparse_score=round(sparse_raw, 6),
                    evidence_score=round(evidence, 6),
                    degraded=degraded,
                    degradation_reason=degradation_reason,
                )
            )

        ordered = sorted(
            hits,
            key=lambda item: (item.rerank_score, item.evidence_score),
            reverse=True,
        )[:limit]
        return RetrievalSearchResult(
            hits=ordered,
            degraded=degraded,
            degradation_reason=degradation_reason,
        )


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [value / norm for value in vector]


def _dot(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must have the same dimension")
    return sum(a * b for a, b in zip(left, right, strict=True))
