from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.app.core.settings import Settings
from backend.app.domain.entities.chunk import ChunkMetadata, DocumentChunk
from backend.app.infrastructure.vectorstore import (
    ChromaDBVectorStore,
    CollectionNotFoundError,
    DocumentNotFoundError,
    EmbeddingDimensionError,
)


def make_settings(tmp_path: Path) -> Settings:
    project_root = tmp_path / "project"
    chromadb_dir = project_root / "data" / "chromadb"
    return Settings(
        _env_file=None,
        environment="test",
        paths={
            "project_root": project_root,
            "data_dir": project_root / "data",
            "chromadb_dir": chromadb_dir,
            "uploads_dir": project_root / "data" / "uploads",
            "logs_dir": project_root / "data" / "logs",
            "cache_dir": project_root / "data" / "cache",
            "embeddings_dir": project_root / "data" / "embeddings",
            "temp_dir": project_root / "data" / "temp",
            "models_dir": project_root / "models",
        },
    )


def make_store(tmp_path: Path) -> ChromaDBVectorStore:
    return ChromaDBVectorStore(settings=make_settings(tmp_path))


def test_default_persist_directory_is_project_local() -> None:
    settings = Settings(_env_file=None)

    assert settings.paths.chromadb_dir == settings.paths.data_dir / "chromadb"


def test_http_mode_uses_configured_chromadb_service(tmp_path: Path) -> None:
    base = make_settings(tmp_path)
    settings = Settings(
        _env_file=None,
        environment="test",
        paths=base.paths.model_dump(),
        vector_store={"mode": "http", "host": "chromadb", "port": 8000},
    )

    with patch(
        "backend.app.infrastructure.vectorstore.chromadb_store.chromadb.HttpClient"
    ) as client_factory:
        store = ChromaDBVectorStore(settings=settings)

    assert store.client is client_factory.return_value
    client_factory.assert_called_once()
    call = client_factory.call_args.kwargs
    assert call["host"] == "chromadb"
    assert call["port"] == 8000
    assert call["ssl"] is False


def make_chunk(
    document_id: str,
    chunk_id: str,
    content: str,
    embedding: list[float],
    *,
    filename: str = "guide.md",
    file_type: str = "markdown",
    domain: str = "spark",
    chunk_index: int = 0,
    tags: list[str] | None = None,
) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        document_id=document_id,
        content=content,
        embedding=embedding,
        metadata=ChunkMetadata(
            document_id=document_id,
            filename=filename,
            file_type=file_type,
            domain=domain,
            chunk_index=chunk_index,
            created_at="2026-06-08T00:00:00Z",
            source=f"knowledge_base/{filename}",
            tags=tags or ["data", domain],
        ),
    )


def unique_collection_name(prefix: str = "kb") -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def test_collection_crud_and_stats(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    collection_name = unique_collection_name()

    store.create_collection(collection_name, metadata={"domain": "spark"})

    assert collection_name in store.list_collections()
    assert store.collection_stats(collection_name).document_count == 0

    store.delete_collection(collection_name)

    assert collection_name not in store.list_collections()
    with pytest.raises(CollectionNotFoundError):
        store.collection_stats(collection_name)


def test_add_get_update_delete_documents(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    collection_name = unique_collection_name()
    store.create_collection(collection_name)
    chunk = make_chunk("doc-1", "chunk-1", "Spark shuffle tuning", [1.0, 0.0, 0.0])

    store.add_documents(collection_name, [chunk])

    stored = store.get_document(collection_name, "chunk-1")
    assert stored.id == "chunk-1"
    assert stored.content == "Spark shuffle tuning"
    assert stored.metadata.document_id == "doc-1"
    assert stored.metadata.tags == ["data", "spark"]
    assert store.collection_stats(collection_name).document_count == 1

    updated = make_chunk(
        "doc-1",
        "chunk-1",
        "Spark AQE tuning",
        [0.5, 0.5, 0.0],
        chunk_index=1,
        tags=["spark", "aqe"],
    )
    store.update_documents(collection_name, [updated])

    stored_after_update = store.get_document(collection_name, "chunk-1")
    assert stored_after_update.content == "Spark AQE tuning"
    assert stored_after_update.metadata.chunk_index == 1
    assert stored_after_update.metadata.tags == ["spark", "aqe"]

    store.delete_documents(collection_name, ["chunk-1"])

    with pytest.raises(DocumentNotFoundError):
        store.get_document(collection_name, "chunk-1")


def test_batch_insert_delete_and_pagination(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    collection_name = unique_collection_name()
    store.create_collection(collection_name)
    chunks = [
        make_chunk("doc", f"chunk-{index}", f"content {index}", [float(index), 1.0])
        for index in range(5)
    ]

    store.add_documents(collection_name, chunks, batch_size=2)

    page = store.list_documents(collection_name, limit=2, offset=1)
    assert [chunk.id for chunk in page] == ["chunk-1", "chunk-2"]

    store.delete_documents(collection_name, ["chunk-1", "chunk-3"], batch_size=1)

    assert store.collection_stats(collection_name).document_count == 3


def test_similarity_search_and_scores(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    collection_name = unique_collection_name()
    store.create_collection(collection_name)
    store.add_documents(
        collection_name,
        [
            make_chunk("doc-1", "near", "near spark", [1.0, 0.0]),
            make_chunk("doc-2", "far", "far mysql", [0.0, 1.0], domain="mysql"),
        ],
    )

    results = store.similarity_search(collection_name, [0.9, 0.1], limit=1)
    scored = store.similarity_search_with_scores(collection_name, [0.9, 0.1], limit=2)

    assert [chunk.id for chunk in results] == ["near"]
    assert scored[0].chunk.id == "near"
    assert scored[0].score >= scored[1].score


def test_metadata_filter_search(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    collection_name = unique_collection_name()
    store.create_collection(collection_name)
    store.add_documents(
        collection_name,
        [
            make_chunk("doc-1", "spark-1", "spark one", [1.0, 0.0], domain="spark"),
            make_chunk("doc-2", "mysql-1", "mysql one", [0.0, 1.0], domain="mysql"),
        ],
    )

    filtered = store.metadata_filter_search(
        collection_name,
        {"domain": "mysql", "file_type": "markdown"},
        limit=10,
    )

    assert [chunk.id for chunk in filtered] == ["mysql-1"]


def test_embedding_dimension_errors_are_reported(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    collection_name = unique_collection_name()
    store.create_collection(collection_name)
    store.add_documents(
        collection_name,
        [make_chunk("doc-1", "chunk-1", "first", [1.0, 0.0])],
    )

    with pytest.raises(EmbeddingDimensionError):
        store.add_documents(
            collection_name,
            [make_chunk("doc-2", "chunk-2", "bad", [1.0, 0.0, 0.0])],
        )

    with pytest.raises(EmbeddingDimensionError):
        store.similarity_search(collection_name, [1.0, 0.0, 0.0])


def test_missing_collection_and_document_errors(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    with pytest.raises(CollectionNotFoundError):
        store.get_document("missing", "chunk")

    collection_name = unique_collection_name()
    store.create_collection(collection_name)

    with pytest.raises(DocumentNotFoundError):
        store.get_document(collection_name, "missing")


def test_persistence_across_store_instances(tmp_path: Path) -> None:
    collection_name = unique_collection_name()
    settings = make_settings(tmp_path)
    first_store = ChromaDBVectorStore(settings=settings)
    first_store.create_collection(collection_name)
    first_store.add_documents(
        collection_name,
        [make_chunk("doc-1", "persisted", "persisted content", [1.0, 0.0])],
    )

    second_store = ChromaDBVectorStore(settings=settings)

    stored = second_store.get_document(collection_name, "persisted")
    assert stored.content == "persisted content"
    assert stored.metadata.domain == "spark"
