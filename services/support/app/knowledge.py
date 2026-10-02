from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain import KnowledgeCitation
from app.knowledge_ingestion import load_retrieval_documents
from app.model_gateway import ModelGatewayError, OpenAICompatibleModel
from app.retrieval import (
    BM25Retriever,
    DeterministicHashEmbedding,
    HybridRetriever,
    InMemoryDenseRetriever,
    RetrievalDocument,
)


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    citations: list[KnowledgeCitation]
    degraded: bool = False
    degradation_reason: str | None = None


def default_knowledge_documents(path: str | None = None) -> list[RetrievalDocument]:
    return load_retrieval_documents(path)


def normalize_retrieval_query(query: str) -> str:
    """Normalize equivalent support terminology before sparse/dense retrieval."""
    normalized = re.sub(r"\b(?:sign|log)[\s_-]*in\b", "login", query, flags=re.IGNORECASE)
    return re.sub(r"(?<=[A-Za-z0-9])[-_](?=[A-Za-z0-9])", " ", normalized)


class KnowledgeService:
    """Grounded knowledge service backed by hybrid retrieval and answerability gating."""

    def __init__(
        self,
        model: OpenAICompatibleModel | None = None,
        *,
        retriever: HybridRetriever | None = None,
        documents: list[RetrievalDocument] | None = None,
        min_evidence_score: float = 0.16,
    ) -> None:
        self.model = model
        self.documents = documents or default_knowledge_documents()
        if retriever is None:
            dense = InMemoryDenseRetriever(
                self.documents,
                DeterministicHashEmbedding(),
            )
            retriever = HybridRetriever(
                self.documents,
                dense_retriever=dense,
                sparse_retriever=BM25Retriever(self.documents),
            )
        self.retriever = retriever
        self.min_evidence_score = min_evidence_score

    def search(self, query: str, limit: int = 3) -> list[KnowledgeCitation]:
        return self.search_with_status(query, limit=limit).citations

    def search_with_status(self, query: str, limit: int = 3) -> KnowledgeSearchResult:
        retrieval_query = normalize_retrieval_query(query)
        result = self.retriever.search_with_status(
            retrieval_query,
            limit=max(limit, 1),
            prefetch=max(10, limit * 3),
        )
        hits = result.hits
        if not hits or hits[0].evidence_score < self.min_evidence_score:
            return KnowledgeSearchResult(
                citations=[],
                degraded=result.degraded,
                degradation_reason=result.degradation_reason,
            )

        accepted = [
            hit
            for hit in hits
            if hit.evidence_score >= self.min_evidence_score
            and hit.evidence_score >= hits[0].evidence_score * 0.55
        ][:limit]
        citations = [
            KnowledgeCitation(
                document_id=hit.document.document_id,
                title=hit.document.title,
                snippet=hit.document.text,
                score=round(hit.evidence_score, 4),
                source_uri=hit.document.source_uri,
                document_version=hit.document.document_version,
                chunk_id=hit.document.chunk_id,
            )
            for hit in accepted
        ]
        return KnowledgeSearchResult(
            citations=citations,
            degraded=result.degraded,
            degradation_reason=result.degradation_reason,
        )

    def answer(self, query: str, citations: list[KnowledgeCitation]) -> str:
        if not citations:
            return "当前知识库没有足够证据回答这个问题，建议转人工客服处理。"
        if self.model is None:
            top = citations[0]
            return f"[{top.document_id}] {top.snippet}"

        evidence = "\n".join(
            f"[{item.document_id}] {item.title}: {item.snippet}" for item in citations
        )
        system = (
            "You are a customer-support answer composer. Answer only from the supplied evidence. "
            "Do not invent policies, order state, refunds, or actions. "
            "If evidence is insufficient, say that human support is required. "
            "Every factual policy claim must retain a supplied source id such as [KB-...]."
        )
        user = f"Question:\n{query}\n\nEvidence:\n{evidence}"
        try:
            return self.model.chat_text(system=system, user=user)
        except ModelGatewayError:
            top = citations[0]
            return f"[{top.document_id}] {top.snippet}"
