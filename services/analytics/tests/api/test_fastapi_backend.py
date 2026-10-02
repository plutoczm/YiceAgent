import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.application.rag.models import Citation, IngestionResult, RAGResponse
from backend.app.core.settings import Settings
from backend.app.domain.ports.llm_provider import (
    LLMHealthStatus,
    LLMUsage,
)
from backend.app.domain.ports.vector_store import CollectionStats
from backend.app.infrastructure.vectorstore.exceptions import CollectionNotFoundError
from backend.app.main import create_app
from backend.app.presentation.api.dependencies.providers import (
    DocumentRegistry,
    get_app_settings,
    get_document_ingestion_service,
    get_document_registry,
    get_llm_provider,
    get_rag_service,
    get_vector_store,
)


class FakeLLM:
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

    def provider_name(self) -> str:
        return "fake-llm"


class FakeVectorStore:
    def __init__(self) -> None:
        self.deleted_ids: list[str] = []

    def collection_stats(self, collection_name: str) -> CollectionStats:
        return CollectionStats(name=collection_name, document_count=2)

    def delete_documents(self, collection_name: str, ids, *, batch_size: int = 100) -> None:
        self.deleted_ids.extend(ids)


class MissingCollectionVectorStore(FakeVectorStore):
    def collection_stats(self, collection_name: str) -> CollectionStats:
        raise CollectionNotFoundError(
            f"Collection '{collection_name}' not found",
            collection_name=collection_name,
        )


class FakeIngestionService:
    async def ingest_file(self, file_path: Path, *, collection_name: str, domain: str, tags=None):
        return IngestionResult(
            document_id="doc-1",
            filename=file_path.name,
            file_type=file_path.suffix.lstrip("."),
            domain=domain,
            stored_path=file_path,
            collection_name=collection_name,
            chunk_count=1,
        )


class RecordingIngestionService(FakeIngestionService):
    def __init__(self) -> None:
        self.seen_path: Path | None = None

    async def ingest_file(self, file_path: Path, *, collection_name: str, domain: str, tags=None):
        self.seen_path = file_path
        assert file_path.exists()
        return await super().ingest_file(
            file_path,
            collection_name=collection_name,
            domain=domain,
            tags=tags,
        )


class FakeRAGService:
    async def answer(
        self,
        question: str,
        *,
        collection_name: str,
        top_k: int = 5,
        metadata_filter=None,
        score_threshold=None,
    ) -> RAGResponse:
        if question == "explode":
            raise RuntimeError("boom")
        return RAGResponse(
            answer="Spark AQE reduces shuffle work.",
            retrieved_chunks=[],
            citations=[
                Citation(
                    document_name="spark.md",
                    chunk_reference="spark.md#chunk-0",
                    similarity_score=0.98,
                    source_metadata={
                        "document_id": "doc-1",
                        "filename": "spark.md",
                        "file_type": "markdown",
                        "domain": "spark",
                        "chunk_index": 0,
                        "created_at": "2026-06-08T00:00:00Z",
                        "source": "spark.md",
                        "tags": ["spark"],
                    },
                )
            ],
            metadata={"collection_name": collection_name, "retrieved_count": 1},
            token_usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


def make_client(
    *,
    settings: Settings | None = None,
    vector_store: FakeVectorStore | None = None,
    ingestion_service: FakeIngestionService | None = None,
) -> TestClient:
    app = create_app()
    registry = DocumentRegistry()
    if settings is not None:
        app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLM()
    app.dependency_overrides[get_vector_store] = lambda: vector_store or FakeVectorStore()
    app.dependency_overrides[get_document_ingestion_service] = (
        lambda: ingestion_service or FakeIngestionService()
    )
    app.dependency_overrides[get_rag_service] = lambda: FakeRAGService()
    app.dependency_overrides[get_document_registry] = lambda: registry
    return TestClient(app, raise_server_exceptions=False)


def test_root_endpoint_returns_service_metadata() -> None:
    client = make_client()

    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "service": "DataPilot-AI",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


def test_health_endpoint_returns_structured_status() -> None:
    client = make_client()

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "DataPilot-AI"
    assert payload["status"] == "ok"
    assert payload["llm_provider"]["provider"] == "fake-llm"
    assert payload["vector_store"]["status"] == "ok"


def test_liveness_endpoint_does_not_probe_dependencies() -> None:
    client = make_client()

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"service": "DataPilot-AI", "status": "ok"}


def test_health_endpoint_degrades_when_default_collection_is_missing() -> None:
    client = make_client(vector_store=MissingCollectionVectorStore())

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["vector_store"]["status"] == "missing"
    assert payload["vector_store"]["collection_name"] == "knowledge_base"
    assert payload["vector_store"]["document_count"] == 0


def test_docs_endpoint_returns_swagger_ui() -> None:
    client = make_client()

    response = client.get("/docs")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Swagger UI" in response.text
    assert "/openapi.json" in response.text


def test_openapi_schema_includes_expected_api_routes() -> None:
    client = make_client()

    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    schema = response.json()
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"] == "DataPilot-AI"
    assert "/health" in schema["paths"]
    assert "/api/v1/chat/stream" in schema["paths"]
    assert "/api/v1/knowledge/documents" in schema["paths"]
    assert "/api/v1/knowledge/query" in schema["paths"]
    assert "/api/v1/text2sql" in schema["paths"]
    assert "/api/v1/sql-review" in schema["paths"]
    assert "/api/v1/warehouse-design" in schema["paths"]
    assert "/api/v1/agent/chat" in schema["paths"]
    assert "/api/v1/agent/chat/stream" in schema["paths"]
    assert "/api/v1/agent/memory/long-term" in schema["paths"]
    assert "/api/v1/agent/memory/rules/{rule_id}" in schema["paths"]
    assert "/api/v1/agent/sessions/{session_id}/state" in schema["paths"]


def test_runtime_config_does_not_expose_secrets() -> None:
    client = make_client()

    response = client.get("/api/v1/config/runtime")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_llm_provider"] == "deepseek"
    assert "api_key" not in json.dumps(payload).lower()
    assert payload["capabilities"]["rag"] is True


def test_document_upload_list_and_delete() -> None:
    client = make_client()

    upload = client.post(
        "/api/v1/knowledge/documents",
        data={"collection_name": "kb", "domain": "spark", "tags": "spark,aqe"},
        files={"file": ("spark.txt", b"Spark AQE reduces shuffle.", "text/plain")},
    )
    assert upload.status_code == 201
    uploaded = upload.json()
    assert uploaded["document_id"] == "doc-1"
    assert uploaded["chunk_count"] == 1

    listed = client.get("/api/v1/knowledge/documents")
    assert listed.status_code == 200
    assert listed.json()["documents"][0]["filename"] == "spark.txt"

    deleted = client.delete("/api/v1/knowledge/documents/doc-1")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_document_upload_stages_file_under_configured_temp_dir() -> None:
    settings = Settings(_env_file=None)
    ingestion_service = RecordingIngestionService()
    client = make_client(settings=settings, ingestion_service=ingestion_service)

    upload = client.post(
        "/api/v1/knowledge/documents",
        data={"collection_name": "kb", "domain": "spark", "tags": "spark,aqe"},
        files={"file": ("spark.txt", b"Spark AQE reduces shuffle.", "text/plain")},
    )

    assert upload.status_code == 201
    assert ingestion_service.seen_path is not None
    assert ingestion_service.seen_path.is_relative_to(settings.paths.temp_dir)


def test_knowledge_query_returns_answer_and_citations() -> None:
    client = make_client()

    response = client.post(
        "/api/v1/knowledge/query",
        json={"question": "How does Spark AQE help?", "collection_name": "kb"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "Spark AQE reduces shuffle work."
    assert payload["citations"][0]["document_name"] == "spark.md"
    assert payload["token_usage"]["total_tokens"] == 15


def test_chat_stream_returns_sse_events() -> None:
    client = make_client()

    with client.stream(
        "POST",
        "/api/v1/chat/stream",
        json={"message": "How does Spark AQE help?", "collection_name": "kb"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: token" in body
    assert "Spark AQE reduces shuffle work." in body
    assert "event: citations" in body
    assert "event: done" in body


def test_validation_errors_use_consistent_response_format() -> None:
    client = make_client()

    response = client.post("/api/v1/knowledge/query", json={"question": ""})

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["message"]
    assert payload["request_id"] == response.headers["x-request-id"]


def test_unhandled_errors_use_consistent_response_format() -> None:
    client = make_client()

    response = client.post(
        "/api/v1/knowledge/query",
        json={"question": "explode", "collection_name": "kb"},
    )

    assert response.status_code == 500
    payload = response.json()
    assert payload["error"]["code"] == "internal_server_error"
    assert payload["error"]["message"] == "服务器内部错误"
    assert payload["request_id"] == response.headers["x-request-id"]
