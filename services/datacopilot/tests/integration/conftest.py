import json
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.application.agent.graph import AgentGraph
from backend.app.application.rag.models import Citation, IngestionResult, RAGResponse
from backend.app.application.sql_review.models import RiskLevel, SQLReviewIssue, SQLReviewResult
from backend.app.application.text2sql.models import SQLEngine, SQLValidationResult, Text2SQLResult
from backend.app.application.warehouse_design.models import WarehouseDesignResult
from backend.app.core.settings import Settings
from backend.app.domain.ports.llm_provider import (
    LLMHealthStatus,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    LLMUsage,
)
from backend.app.domain.ports.vector_store import CollectionStats
from backend.app.main import create_app
from backend.app.presentation.api.dependencies.providers import (
    DocumentRegistry,
    get_agent_graph,
    get_app_settings,
    get_document_ingestion_service,
    get_document_registry,
    get_llm_provider,
    get_rag_service,
    get_sql_review_service,
    get_text2sql_service,
    get_vector_store,
    get_warehouse_design_service,
)
from frontend.services.backend_client import BackendClient


SCHEMA_CONTEXT = """
dwd_user_behavior_detail(
    user_id bigint,
    event_name string,
    dt string
)

dwd_order_detail(
    order_id bigint,
    user_id bigint,
    pay_amount decimal(18,2),
    dt string
)
"""


class IntegrationState:
    def __init__(self) -> None:
        self.uploaded_documents: dict[str, IngestionResult] = {}
        self.uploaded_text: dict[str, str] = {}


class IntegrationLLMProvider:
    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        prompt = "\n".join(message.content for message in messages)
        if "Hive-compatible" in prompt:
            content = json.dumps({"recommendations": ["Partition all fact tables by dt."]})
        elif "Return JSON only" in prompt or "Target engine" in prompt:
            content = json.dumps(
                {
                    "sql": (
                        "SELECT COUNT(DISTINCT user_id) AS active_users "
                        "FROM dwd_user_behavior_detail "
                        "WHERE dt >= date_sub(current_date, 7)"
                    ),
                    "explanation": "Count distinct active users in the last seven days.",
                    "optimization_suggestions": ["Use dt partition pruning."],
                    "confidence": 0.92,
                }
            )
        elif "Rules are authoritative" in prompt:
            content = "The SQL review found scan and partition considerations."
        else:
            content = "Spark AQE uses runtime statistics to optimize joins, shuffles, and partitions."
        return LLMResponse(
            provider="integration-llm",
            model="integration-model",
            content=content,
            usage=LLMUsage(prompt_tokens=20, completion_tokens=12, total_tokens=32),
        )

    async def stream_chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        yield LLMStreamChunk(
            provider="integration-llm",
            model="integration-model",
            content="Spark AQE ",
        )
        yield LLMStreamChunk(
            provider="integration-llm",
            model="integration-model",
            content="optimizes runtime plans.",
            finish_reason="stop",
            usage=LLMUsage(prompt_tokens=5, completion_tokens=4, total_tokens=9),
        )

    async def health_check(self) -> LLMHealthStatus:
        return LLMHealthStatus(
            provider="integration-llm",
            ok=True,
            api_key_configured=True,
            reachable=True,
            model_available=True,
            model="integration-model",
            message="ok",
        )

    def token_count(self, text_or_messages) -> int:
        if isinstance(text_or_messages, str):
            return len(text_or_messages.split())
        return sum(len(message.content.split()) for message in text_or_messages)

    def provider_name(self) -> str:
        return "integration-llm"


class IntegrationVectorStore:
    def __init__(self, state: IntegrationState) -> None:
        self.state = state

    def collection_stats(self, collection_name: str) -> CollectionStats:
        return CollectionStats(name=collection_name, document_count=len(self.state.uploaded_documents))

    def delete_documents(self, collection_name: str, ids, *, batch_size: int = 100) -> None:
        for document_id in ids:
            self.state.uploaded_documents.pop(document_id, None)
            self.state.uploaded_text.pop(document_id, None)


class IntegrationIngestionService:
    def __init__(self, state: IntegrationState) -> None:
        self.state = state

    async def ingest_file(
        self,
        file_path: Path,
        *,
        collection_name: str,
        domain: str,
        tags=None,
    ) -> IngestionResult:
        document_id = f"doc-{len(self.state.uploaded_documents) + 1}"
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        result = IngestionResult(
            document_id=document_id,
            filename=file_path.name,
            file_type=file_path.suffix.lstrip(".") or "txt",
            domain=domain,
            stored_path=file_path,
            collection_name=collection_name,
            chunk_count=1,
        )
        self.state.uploaded_documents[document_id] = result
        self.state.uploaded_text[document_id] = text
        return result


class IntegrationRAGService:
    def __init__(self, state: IntegrationState) -> None:
        self.state = state

    async def answer(
        self,
        question: str,
        *,
        collection_name: str,
        top_k: int = 5,
        metadata_filter=None,
        score_threshold=None,
    ) -> RAGResponse:
        document = next(iter(self.state.uploaded_documents.values()), None)
        if document is None:
            return RAGResponse(
                answer="I do not have enough retrieved context to answer this question reliably.",
                retrieved_chunks=[],
                citations=[],
                metadata={"collection_name": collection_name, "retrieved_count": 0},
            )
        return RAGResponse(
            answer=(
                "Spark AQE uses runtime statistics to optimize joins, shuffle partitions, "
                "and skew handling."
            ),
            retrieved_chunks=[],
            citations=[
                Citation(
                    document_name=document.filename,
                    chunk_reference=f"{document.filename}#chunk-0",
                    similarity_score=0.96,
                    source_metadata={
                        "document_id": document.document_id,
                        "filename": document.filename,
                        "file_type": document.file_type,
                        "domain": document.domain,
                        "chunk_index": 0,
                        "source": str(document.stored_path),
                        "tags": ["spark", "aqe"],
                    },
                )
            ],
            metadata={"collection_name": collection_name, "retrieved_count": 1},
            token_usage=LLMUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
        )


class IntegrationText2SQLService:
    async def generate(self, **kwargs) -> Text2SQLResult:
        return Text2SQLResult(
            sql=(
                "SELECT COUNT(DISTINCT user_id) AS active_users "
                "FROM dwd_user_behavior_detail "
                "WHERE dt >= date_sub(current_date, 7)"
            ),
            explanation="Count distinct active users in the last seven days.",
            optimization_suggestions=["Use dt partition pruning."],
            engine=kwargs.get("engine", SQLEngine.HIVE),
            confidence=0.92,
            validation=SQLValidationResult(is_valid=True),
            token_usage=LLMUsage(prompt_tokens=20, completion_tokens=15, total_tokens=35),
            metadata={"schema_table_count": 1},
        )


class IntegrationSQLReviewService:
    async def review(self, **kwargs) -> SQLReviewResult:
        sql = kwargs["sql"]
        issues = []
        if "select *" in sql.lower():
            issues.append(
                SQLReviewIssue(
                    code="select_star",
                    title="SELECT * detected",
                    description="Avoid scanning unnecessary columns.",
                    severity=RiskLevel.MEDIUM,
                    suggestion="Select explicit columns.",
                )
            )
        return SQLReviewResult(
            risk_level=RiskLevel.MEDIUM if issues else RiskLevel.LOW,
            score=76 if issues else 94,
            issues=issues,
            optimization_suggestions=["Keep dt partition filters on large tables."],
            llm_explanation="The review validates partition and scan efficiency.",
            engine=kwargs.get("engine", SQLEngine.HIVE),
            token_usage=LLMUsage(prompt_tokens=12, completion_tokens=8, total_tokens=20),
        )


class IntegrationWarehouseDesignService:
    async def design(self, **kwargs) -> WarehouseDesignResult:
        result = WarehouseDesignResult.example(kwargs["requirement"])
        result.metadata["integration"] = True
        return result


class TestClientAdapter:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def get(self, path: str, **kwargs):
        return self.client.get(path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.client.post(path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.client.delete(path, **kwargs)

    def stream(self, method: str, path: str, **kwargs):
        return self.client.stream(method, path, **kwargs)


@pytest.fixture
def integration_state() -> IntegrationState:
    return IntegrationState()


@pytest.fixture
def integration_services(integration_state: IntegrationState) -> dict[str, Any]:
    llm = IntegrationLLMProvider()
    rag = IntegrationRAGService(integration_state)
    text2sql = IntegrationText2SQLService()
    sql_review = IntegrationSQLReviewService()
    warehouse = IntegrationWarehouseDesignService()
    agent = AgentGraph(
        rag_service=rag,
        text2sql_service=text2sql,
        sql_review_service=sql_review,
        warehouse_design_service=warehouse,
        llm_provider=llm,
    )
    return {
        "llm": llm,
        "vector_store": IntegrationVectorStore(integration_state),
        "ingestion": IntegrationIngestionService(integration_state),
        "rag": rag,
        "text2sql": text2sql,
        "sql_review": sql_review,
        "warehouse": warehouse,
        "agent": agent,
    }


@pytest.fixture
def app_client(integration_services: dict[str, Any]) -> Iterator[TestClient]:
    app = create_app()
    registry = DocumentRegistry()
    settings = Settings(_env_file=None)
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_llm_provider] = lambda: integration_services["llm"]
    app.dependency_overrides[get_vector_store] = lambda: integration_services["vector_store"]
    app.dependency_overrides[get_document_ingestion_service] = lambda: integration_services["ingestion"]
    app.dependency_overrides[get_document_registry] = lambda: registry
    app.dependency_overrides[get_rag_service] = lambda: integration_services["rag"]
    app.dependency_overrides[get_text2sql_service] = lambda: integration_services["text2sql"]
    app.dependency_overrides[get_sql_review_service] = lambda: integration_services["sql_review"]
    app.dependency_overrides[get_warehouse_design_service] = lambda: integration_services["warehouse"]
    app.dependency_overrides[get_agent_graph] = lambda: integration_services["agent"]
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def frontend_backend_client(app_client: TestClient) -> BackendClient:
    return BackendClient(base_url="http://testserver", http_client=TestClientAdapter(app_client))
