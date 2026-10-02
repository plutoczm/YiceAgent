import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from backend.app.application.rag.chunking_service import ChunkingService
from backend.app.application.rag.citation_service import CitationService
from backend.app.application.rag.document_ingestion_service import DocumentIngestionService
from backend.app.application.rag.rag_service import RAGService
from backend.app.application.rag.retrieval_service import RetrievalService
from backend.app.core.settings import Settings
from backend.app.domain.ports.llm_provider import (
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
)
from backend.app.infrastructure.document_loaders import DocumentLoaderFactory
from backend.app.infrastructure.embeddings import HashingEmbeddingProvider
from backend.app.infrastructure.vectorstore import ChromaDBVectorStore


pytestmark = pytest.mark.anyio
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class GoldenAnswerLLM:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.last_prompt = ""

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_type: str | None = None,
    ) -> LLMResponse:
        self.last_prompt = messages[-1].content
        return LLMResponse(provider="golden", model="offline-eval", content=self.answer)

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_type: str | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        yield LLMStreamChunk(provider="golden", model="offline-eval", content=self.answer)

    def token_count(self, text_or_messages) -> int:
        return 0

    def provider_name(self) -> str:
        return "golden"


def load_cases() -> list[dict]:
    return json.loads(
        (PROJECT_ROOT / "evaluation" / "rag_cases.json").read_text(encoding="utf-8")
    )


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
        logging={"file_enabled": False},
    )


async def build_pipeline(tmp_path: Path):
    settings = make_settings(tmp_path)
    embedding = HashingEmbeddingProvider(settings, dimension=256)
    store = ChromaDBVectorStore(settings)
    ingestion = DocumentIngestionService(
        loader_factory=DocumentLoaderFactory(),
        chunking_service=ChunkingService(chunk_size=800, chunk_overlap=120),
        embedding_provider=embedding,
        vector_store=store,
        uploads_dir=settings.paths.uploads_dir,
    )
    for case in load_cases():
        await ingestion.ingest_file(
            PROJECT_ROOT / case["source_path"],
            collection_name="rag_evaluation",
            domain=case["domain"],
            tags=["offline-evaluation", case["id"]],
        )
    return RetrievalService(embedding_provider=embedding, vector_store=store)


async def test_retrieval_cases_hit_expected_document_at_top_one(tmp_path: Path) -> None:
    retrieval = await build_pipeline(tmp_path)

    for case in load_cases():
        results = await retrieval.retrieve(
            case["question"],
            collection_name="rag_evaluation",
            top_k=1,
            metadata_filter={"domain": case["domain"]},
            retrieval_mode="hybrid",
        )

        assert results, case["id"]
        assert results[0].chunk.metadata.filename == case["expected_filename"]
        assert results[0].chunk.metadata.domain == case["domain"]


async def test_answer_quality_contract_is_grounded_and_cited(tmp_path: Path) -> None:
    retrieval = await build_pipeline(tmp_path)

    for case in load_cases():
        llm = GoldenAnswerLLM(case["golden_answer"])
        rag = RAGService(
            retrieval_service=retrieval,
            citation_service=CitationService(),
            llm_provider=llm,
        )
        response = await rag.answer(
            case["question"],
            collection_name="rag_evaluation",
            top_k=2,
            metadata_filter={"domain": case["domain"]},
        )

        assert all(term in response.answer for term in case["required_answer_terms"])
        assert response.citations
        assert response.citations[0].document_name == case["expected_filename"]
        assert "Answer only using the retrieved context" in llm.last_prompt
        assert case["expected_filename"] in llm.last_prompt


async def test_answer_refuses_when_retrieval_returns_no_context(tmp_path: Path) -> None:
    retrieval = await build_pipeline(tmp_path)
    rag = RAGService(
        retrieval_service=retrieval,
        citation_service=CitationService(),
        llm_provider=GoldenAnswerLLM("should not be used"),
    )

    response = await rag.answer(
        "完全无关的问题",
        collection_name="rag_evaluation",
        top_k=2,
        metadata_filter={"domain": "missing-domain"},
    )

    assert "not have enough retrieved context" in response.answer
    assert response.retrieved_chunks == []
    assert response.citations == []
