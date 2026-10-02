import math
import re
from collections import Counter

from backend.app.application.rag.models import RetrievedChunk, RetrievalMode
from backend.app.domain.entities.chunk import DocumentChunk
from backend.app.domain.ports.embedding_provider import EmbeddingProvider
from backend.app.domain.ports.vector_store import VectorStore


class RetrievalService:
    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
    ) -> None:
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store

    async def retrieve(
        self,
        question: str,
        *,
        collection_name: str,
        top_k: int = 5,
        metadata_filter: dict[str, str | int | float | bool] | None = None,
        score_threshold: float | None = None,
        retrieval_mode: RetrievalMode | str = RetrievalMode.HYBRID,
    ) -> list[RetrievedChunk]:
        mode = RetrievalMode(retrieval_mode)
        query_embedding = await self.embedding_provider.embed_query(question)
        candidate_limit = top_k if mode is RetrievalMode.VECTOR else max(top_k * 4, 20)
        results = self.vector_store.similarity_search_with_scores(
            collection_name,
            query_embedding.embedding,
            limit=candidate_limit,
            metadata_filter=metadata_filter,
        )
        if mode is RetrievalMode.VECTOR:
            retrieved = [
                RetrievedChunk(
                    chunk=result.chunk,
                    score=result.score,
                    dense_score=result.score,
                    rerank_score=result.score,
                )
                for result in results[:top_k]
            ]
        else:
            documents = self._candidate_documents(
                collection_name,
                metadata_filter=metadata_filter,
                limit=1000,
            )
            retrieved = self._hybrid_rank(question, results, documents, top_k=top_k)
        if score_threshold is not None:
            retrieved = [
                item for item in retrieved if item.score >= score_threshold
            ]
        return retrieved

    def _candidate_documents(
        self,
        collection_name: str,
        *,
        metadata_filter: dict[str, str | int | float | bool] | None,
        limit: int,
    ) -> list[DocumentChunk]:
        if metadata_filter:
            return self.vector_store.metadata_filter_search(
                collection_name, metadata_filter, limit=limit
            )
        return self.vector_store.list_documents(collection_name, limit=limit)

    def _hybrid_rank(self, question, dense_results, documents, *, top_k):
        lexical = _bm25_scores(question, documents)
        dense_by_id = {result.chunk.id: result for result in dense_results}
        lexical_by_id = {chunk.id: score for chunk, score in lexical}
        chunks = {result.chunk.id: result.chunk for result in dense_results}
        chunks.update({chunk.id: chunk for chunk, _ in lexical})

        dense_rank = {result.chunk.id: rank for rank, result in enumerate(dense_results, 1)}
        lexical_rank = {chunk.id: rank for rank, (chunk, _) in enumerate(lexical, 1)}
        fused: list[tuple[DocumentChunk, float, float, float]] = []
        for chunk_id, chunk in chunks.items():
            rrf = 0.0
            if chunk_id in dense_rank:
                rrf += 0.6 / (60 + dense_rank[chunk_id])
            if chunk_id in lexical_rank:
                rrf += 0.4 / (60 + lexical_rank[chunk_id])
            dense_score = dense_by_id[chunk_id].score if chunk_id in dense_by_id else 0.0
            lexical_score = lexical_by_id.get(chunk_id, 0.0)
            fused.append((chunk, rrf, dense_score, lexical_score))

        max_rrf = max((item[1] for item in fused), default=1.0) or 1.0
        max_lexical = max((item[3] for item in fused), default=1.0) or 1.0
        query_terms = set(_tokenize(question))
        ranked: list[RetrievedChunk] = []
        for chunk, rrf, dense_score, lexical_score in fused:
            content_terms = set(_tokenize(chunk.content))
            overlap = len(query_terms & content_terms) / max(len(query_terms), 1)
            normalized_rrf = rrf / max_rrf
            normalized_lexical = lexical_score / max_lexical
            rerank_score = 0.70 * normalized_rrf + 0.20 * overlap + 0.10 * normalized_lexical
            ranked.append(
                RetrievedChunk(
                    chunk=chunk,
                    score=min(1.0, rerank_score),
                    dense_score=dense_score,
                    lexical_score=normalized_lexical,
                    rerank_score=rerank_score,
                )
            )
        return sorted(ranked, key=lambda item: item.score, reverse=True)[:top_k]


def _bm25_scores(question: str, documents: list[DocumentChunk]):
    query_terms = _tokenize(question)
    tokenized = [_tokenize(chunk.content) for chunk in documents]
    if not query_terms or not tokenized:
        return []
    document_frequency = Counter(
        term for terms in tokenized for term in set(terms)
    )
    average_length = sum(len(terms) for terms in tokenized) / len(tokenized)
    scored = []
    for chunk, terms in zip(documents, tokenized, strict=True):
        frequencies = Counter(terms)
        score = 0.0
        for term in query_terms:
            frequency = frequencies[term]
            if not frequency:
                continue
            idf = math.log(1 + (len(documents) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * len(terms) / max(average_length, 1))
            score += idf * frequency * 2.5 / denominator
        if score > 0:
            scored.append((chunk, score))
    return sorted(scored, key=lambda item: item[1], reverse=True)


def _tokenize(text: str) -> list[str]:
    lowered = text.lower()
    words = re.findall(r"[a-z0-9_]+", lowered)
    chinese = re.findall(r"[\u4e00-\u9fff]", lowered)
    return words + chinese + ["".join(chinese[index : index + 2]) for index in range(len(chinese) - 1)]
