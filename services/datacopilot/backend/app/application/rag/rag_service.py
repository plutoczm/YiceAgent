from backend.app.application.rag.citation_service import CitationService
from backend.app.application.rag.models import RAGResponse, RetrievalMode
from backend.app.application.rag.retrieval_service import RetrievalService
from backend.app.domain.ports.llm_provider import LLMMessage, LLMProvider


RAG_PROMPT_TEMPLATE = """You are DataPilot-AI, a data engineering copilot.

Rules:
1. Answer only using the retrieved context.
2. If the context is insufficient, explicitly state uncertainty.
3. Never hallucinate technical facts.
4. Cite sources by using the provided citation labels.

Question:
{question}

Retrieved context:
{context}

Answer with concise technical accuracy.
"""


class RAGService:
    def __init__(
        self,
        *,
        retrieval_service: RetrievalService,
        citation_service: CitationService,
        llm_provider: LLMProvider,
    ) -> None:
        self.retrieval_service = retrieval_service
        self.citation_service = citation_service
        self.llm_provider = llm_provider

    async def answer(
        self,
        question: str,
        *,
        collection_name: str,
        top_k: int = 5,
        metadata_filter: dict[str, str | int | float | bool] | None = None,
        score_threshold: float | None = None,
        retrieval_mode: RetrievalMode | str = RetrievalMode.HYBRID,
    ) -> RAGResponse:
        retrieved = await self.retrieval_service.retrieve(
            question,
            collection_name=collection_name,
            top_k=top_k,
            metadata_filter=metadata_filter,
            score_threshold=score_threshold,
            retrieval_mode=retrieval_mode,
        )
        citations = self.citation_service.create_citations(retrieved)
        if not retrieved:
            return RAGResponse(
                answer="I do not have enough retrieved context to answer this question reliably.",
                retrieved_chunks=[],
                citations=[],
                metadata={
                    "collection_name": collection_name,
                    "retrieved_count": 0,
                    "retrieval_mode": RetrievalMode(retrieval_mode).value,
                },
            )

        prompt = RAG_PROMPT_TEMPLATE.format(
            question=question,
            context=self._assemble_context(retrieved),
        )
        llm_response = await self.llm_provider.chat(
            [
                LLMMessage(
                    role="system",
                    content="You answer grounded data engineering questions with citations.",
                ),
                LLMMessage(role="user", content=prompt),
            ],
            temperature=0.0,
        )
        return RAGResponse(
            answer=llm_response.content,
            retrieved_chunks=[item.chunk for item in retrieved],
            citations=citations,
            metadata={
                "collection_name": collection_name,
                "retrieved_count": len(retrieved),
                "retrieval_mode": RetrievalMode(retrieval_mode).value,
                "llm_provider": llm_response.provider,
                "llm_model": llm_response.model,
            },
            token_usage=llm_response.usage,
        )

    def _assemble_context(self, retrieved) -> str:
        sections: list[str] = []
        for index, item in enumerate(retrieved, start=1):
            metadata = item.chunk.metadata
            sections.append(
                "\n".join(
                    [
                        f"[source-{index}] {metadata.filename}#chunk-{metadata.chunk_index}",
                        f"score: {item.score:.4f}",
                        f"domain: {metadata.domain}",
                        item.chunk.content,
                    ]
                )
            )
        return "\n\n".join(sections)
