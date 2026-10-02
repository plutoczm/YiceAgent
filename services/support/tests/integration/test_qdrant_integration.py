from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest

from app.knowledge_ingestion import KnowledgeSourceDocument, build_retrieval_documents
from app.retrieval import (
    DeterministicHashEmbedding,
    OpenAICompatibleEmbedding,
    QdrantDenseRetriever,
    RetrievalBackendError,
    RetrievalFailureReason,
)

pytest.importorskip("qdrant_client")
if os.getenv("RUN_QDRANT_INTEGRATION") != "1":
    pytest.skip(
        "set RUN_QDRANT_INTEGRATION=1 to run Qdrant integration tests",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration


class _EmbeddingHandler(BaseHTTPRequestHandler):
    provider = DeterministicHashEmbedding(dimension=64)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path != "/v1/embeddings":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        inputs = payload.get("input", [])
        vectors = self.provider.embed([str(item) for item in inputs])
        response = {
            "object": "list",
            "data": [
                {"object": "embedding", "index": index, "embedding": vector}
                for index, vector in enumerate(vectors)
            ],
            "model": payload.get("model", "mock-embedding"),
        }
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:
        del format, args


@contextmanager
def _mock_embedding_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _documents(*, refund_text: str, include_return: bool = True):
    sources = [
        KnowledgeSourceDocument(
            document_id="KB-REFUND-INTEGRATION",
            version="1",
            title="Refund integration policy",
            text=refund_text,
            source_uri="supportops://integration/refund",
        )
    ]
    if include_return:
        sources.append(
            KnowledgeSourceDocument(
                document_id="KB-RETURN-INTEGRATION",
                version="1",
                title="Return integration policy",
                text="return policy requires customer confirmation after delivery",
                source_uri="supportops://integration/return",
            )
        )
    return build_retrieval_documents(sources)


def _wait_for_qdrant() -> None:
    last_error: Exception | None = None
    for _ in range(30):
        try:
            response = httpx.get("http://127.0.0.1:6333/readyz", timeout=0.5)
            if response.status_code == 200:
                return
        except httpx.HTTPError as exc:
            last_error = exc
        threading.Event().wait(0.2)
    raise RuntimeError("Qdrant did not become ready") from last_error


def test_qdrant_sync_is_incremental_and_deletes_stale_chunks():
    _wait_for_qdrant()
    collection = f"supportops_ci_{uuid4().hex}"
    with _mock_embedding_server() as embedding_url:
        provider = OpenAICompatibleEmbedding(
            base_url=embedding_url,
            model="mock-embedding",
            dimension=64,
            timeout_seconds=1.0,
        )
        initial = _documents(refund_text="refund policy requires explicit confirmation")
        first = QdrantDenseRetriever(
            initial,
            provider,
            url="http://127.0.0.1:6333",
            collection=collection,
            timeout_seconds=1.0,
        )
        assert first.sync_stats is not None
        assert first.sync_stats.embedded_chunks == 2
        assert first.sync_stats.upserted_chunks == 2
        assert first.sync_stats.deleted_chunks == 0

        second = QdrantDenseRetriever(
            initial,
            provider,
            url="http://127.0.0.1:6333",
            collection=collection,
            timeout_seconds=1.0,
        )
        assert second.sync_stats is not None
        assert second.sync_stats.embedded_chunks == 0
        assert second.sync_stats.upserted_chunks == 0
        assert second.sync_stats.unchanged_chunks == 2

        changed = _documents(
            refund_text="updated refund policy requires explicit confirmation",
            include_return=False,
        )
        third = QdrantDenseRetriever(
            changed,
            provider,
            url="http://127.0.0.1:6333",
            collection=collection,
            timeout_seconds=1.0,
        )
        assert third.sync_stats is not None
        assert third.sync_stats.embedded_chunks == 1
        assert third.sync_stats.upserted_chunks == 1
        assert third.sync_stats.deleted_chunks == 1
        assert third.sync_stats.unchanged_chunks == 0

        hits = third.search("updated refund policy", limit=2)
        assert hits
        assert hits[0][0].document_id == "KB-REFUND-INTEGRATION"
        assert hits[0][0].source_uri == "supportops://integration/refund"


def test_qdrant_search_outage_is_typed_after_successful_index_sync():
    _wait_for_qdrant()
    collection = f"supportops_ci_{uuid4().hex}"
    with _mock_embedding_server() as embedding_url:
        provider = OpenAICompatibleEmbedding(
            base_url=embedding_url,
            model="mock-embedding",
            dimension=64,
            timeout_seconds=1.0,
        )
        retriever = QdrantDenseRetriever(
            _documents(refund_text="refund policy requires explicit confirmation"),
            provider,
            url="http://127.0.0.1:6333",
            collection=collection,
            timeout_seconds=1.0,
        )

        class BrokenClient:
            def query_points(self, **kwargs):
                del kwargs
                raise ConnectionError("simulated Qdrant outage")

        retriever.client = BrokenClient()
        with pytest.raises(RetrievalBackendError) as exc_info:
            retriever.search("refund policy", limit=2)
        assert exc_info.value.reason is RetrievalFailureReason.QDRANT_UNAVAILABLE
