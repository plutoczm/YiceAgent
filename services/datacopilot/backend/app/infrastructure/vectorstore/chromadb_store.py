import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, TypeVar, cast

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.api.models.Collection import Collection

from backend.app.core.settings import Settings, VectorStoreMode
from backend.app.domain.entities.chunk import ChunkMetadata, DocumentChunk
from backend.app.domain.ports.vector_store import CollectionStats, VectorSearchResult
from backend.app.infrastructure.vectorstore.exceptions import (
    CollectionNotFoundError,
    DocumentNotFoundError,
    EmbeddingDimensionError,
    StorageError,
    VectorStoreError,
)


MetadataValue = str | int | float | bool
Metadata = dict[str, MetadataValue]
T = TypeVar("T")


class ChromaDBVectorStore:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self.persist_directory = Path(settings.paths.chromadb_dir)
        self.client = client or self._build_client()
        self._collections: dict[str, Collection] = {}
        self._dimensions: dict[str, int] = {}

    def _build_client(self) -> Any:
        chroma_settings = ChromaSettings(anonymized_telemetry=False)
        if self.settings.vector_store.mode is VectorStoreMode.HTTP:
            return chromadb.HttpClient(
                host=self.settings.vector_store.host,
                port=self.settings.vector_store.port,
                ssl=self.settings.vector_store.ssl,
                settings=chroma_settings,
            )
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(
            path=str(self.persist_directory),
            settings=chroma_settings,
        )

    def create_collection(
        self,
        name: str,
        metadata: Metadata | None = None,
    ) -> None:
        try:
            collection = self.client.get_or_create_collection(
                name=name,
                metadata=metadata,
                embedding_function=None,
            )
        except Exception as exc:
            raise StorageError(
                f"Failed to create collection '{name}'",
                collection_name=name,
            ) from exc
        self._collections[name] = collection

    def delete_collection(self, name: str) -> None:
        self._require_collection(name)
        try:
            self.client.delete_collection(name)
        except Exception as exc:
            raise StorageError(
                f"Failed to delete collection '{name}'",
                collection_name=name,
            ) from exc
        self._collections.pop(name, None)
        self._dimensions.pop(name, None)

    def list_collections(self) -> list[str]:
        try:
            collections = self.client.list_collections()
        except Exception as exc:
            raise StorageError("Failed to list collections") from exc
        names: list[str] = []
        for collection in collections:
            if isinstance(collection, str):
                names.append(collection)
            else:
                names.append(collection.name)
        return sorted(names)

    def add_documents(
        self,
        collection_name: str,
        chunks: Sequence[DocumentChunk],
        *,
        batch_size: int = 100,
    ) -> None:
        if not chunks:
            return
        collection = self._require_collection(collection_name)
        self._validate_batch_dimensions(collection_name, chunks)
        for batch in _batched(chunks, batch_size):
            try:
                collection.add(
                    ids=[chunk.id for chunk in batch],
                    documents=[chunk.content for chunk in batch],
                    embeddings=[chunk.embedding for chunk in batch],
                    metadatas=[self._metadata_to_chroma(chunk.metadata) for chunk in batch],
                )
            except Exception as exc:
                raise StorageError(
                    f"Failed to add documents to collection '{collection_name}'",
                    collection_name=collection_name,
                ) from exc

    def update_documents(
        self,
        collection_name: str,
        chunks: Sequence[DocumentChunk],
        *,
        batch_size: int = 100,
    ) -> None:
        if not chunks:
            return
        collection = self._require_collection(collection_name)
        self._validate_batch_dimensions(collection_name, chunks)
        for batch in _batched(chunks, batch_size):
            try:
                collection.update(
                    ids=[chunk.id for chunk in batch],
                    documents=[chunk.content for chunk in batch],
                    embeddings=[chunk.embedding for chunk in batch],
                    metadatas=[self._metadata_to_chroma(chunk.metadata) for chunk in batch],
                )
            except Exception as exc:
                raise StorageError(
                    f"Failed to update documents in collection '{collection_name}'",
                    collection_name=collection_name,
                ) from exc

    def delete_documents(
        self,
        collection_name: str,
        ids: Sequence[str],
        *,
        batch_size: int = 100,
    ) -> None:
        if not ids:
            return
        collection = self._require_collection(collection_name)
        for batch in _batched(ids, batch_size):
            try:
                collection.delete(ids=list(batch))
            except Exception as exc:
                raise StorageError(
                    f"Failed to delete documents from collection '{collection_name}'",
                    collection_name=collection_name,
                ) from exc

    def list_documents(
        self,
        collection_name: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentChunk]:
        collection = self._require_collection(collection_name)
        try:
            result = collection.get(
                limit=limit,
                offset=offset,
                include=["documents", "embeddings", "metadatas"],
            )
        except Exception as exc:
            raise StorageError(
                f"Failed to list documents in collection '{collection_name}'",
                collection_name=collection_name,
            ) from exc
        return self._chunks_from_get_result(result)

    def get_document(self, collection_name: str, document_id: str) -> DocumentChunk:
        collection = self._require_collection(collection_name)
        try:
            result = collection.get(
                ids=[document_id],
                include=["documents", "embeddings", "metadatas"],
            )
        except Exception as exc:
            raise StorageError(
                f"Failed to get document '{document_id}'",
                collection_name=collection_name,
                document_id=document_id,
            ) from exc
        chunks = self._chunks_from_get_result(result)
        if not chunks:
            raise DocumentNotFoundError(
                f"Document '{document_id}' not found in collection '{collection_name}'",
                collection_name=collection_name,
                document_id=document_id,
            )
        return chunks[0]

    def similarity_search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        *,
        limit: int = 5,
        metadata_filter: Metadata | None = None,
    ) -> list[DocumentChunk]:
        return [
            result.chunk
            for result in self.similarity_search_with_scores(
                collection_name,
                query_vector,
                limit=limit,
                metadata_filter=metadata_filter,
            )
        ]

    def similarity_search_with_scores(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        *,
        limit: int = 5,
        metadata_filter: Metadata | None = None,
    ) -> list[VectorSearchResult]:
        collection = self._require_collection(collection_name)
        self._validate_query_dimension(collection_name, query_vector)
        try:
            result = collection.query(
                query_embeddings=[list(query_vector)],
                n_results=limit,
                where=self._metadata_filter_to_chroma(metadata_filter),
                include=["documents", "embeddings", "metadatas", "distances"],
            )
        except Exception as exc:
            raise StorageError(
                f"Failed to query collection '{collection_name}'",
                collection_name=collection_name,
            ) from exc
        return self._search_results_from_query_result(result)

    def metadata_filter_search(
        self,
        collection_name: str,
        metadata_filter: Metadata,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentChunk]:
        collection = self._require_collection(collection_name)
        try:
            result = collection.get(
                where=self._metadata_filter_to_chroma(metadata_filter),
                limit=limit,
                offset=offset,
                include=["documents", "embeddings", "metadatas"],
            )
        except Exception as exc:
            raise StorageError(
                f"Failed metadata filter search in collection '{collection_name}'",
                collection_name=collection_name,
            ) from exc
        return self._chunks_from_get_result(result)

    def collection_stats(self, collection_name: str) -> CollectionStats:
        collection = self._require_collection(collection_name)
        try:
            count = collection.count()
            metadata = cast(Metadata, collection.metadata or {})
        except Exception as exc:
            raise StorageError(
                f"Failed to get stats for collection '{collection_name}'",
                collection_name=collection_name,
            ) from exc
        return CollectionStats(
            name=collection_name,
            document_count=count,
            metadata=metadata,
        )

    def _require_collection(self, name: str) -> Collection:
        if name in self._collections:
            return self._collections[name]
        try:
            collection = self.client.get_collection(name, embedding_function=None)
        except Exception as exc:
            raise CollectionNotFoundError(
                f"Collection '{name}' not found",
                collection_name=name,
            ) from exc
        self._collections[name] = collection
        self._dimensions[name] = self._load_collection_dimension(collection)
        return collection

    def _validate_batch_dimensions(
        self,
        collection_name: str,
        chunks: Sequence[DocumentChunk],
    ) -> None:
        dimensions = {len(chunk.embedding) for chunk in chunks}
        if len(dimensions) != 1:
            raise EmbeddingDimensionError(
                "All embeddings in a batch must have the same dimension",
                collection_name=collection_name,
            )
        incoming_dimension = dimensions.pop()
        known_dimension = self._dimensions.get(collection_name)
        if known_dimension is None:
            known_dimension = self._load_collection_dimension(
                self._require_collection(collection_name)
            )
        if known_dimension is not None and incoming_dimension != known_dimension:
            raise EmbeddingDimensionError(
                f"Embedding dimension {incoming_dimension} does not match "
                f"collection dimension {known_dimension}",
                collection_name=collection_name,
            )
        self._dimensions[collection_name] = incoming_dimension

    def _validate_query_dimension(
        self,
        collection_name: str,
        query_vector: Sequence[float],
    ) -> None:
        known_dimension = self._dimensions.get(collection_name)
        if known_dimension is None:
            known_dimension = self._load_collection_dimension(
                self._require_collection(collection_name)
            )
            if known_dimension is not None:
                self._dimensions[collection_name] = known_dimension
        if known_dimension is not None and len(query_vector) != known_dimension:
            raise EmbeddingDimensionError(
                f"Query embedding dimension {len(query_vector)} does not match "
                f"collection dimension {known_dimension}",
                collection_name=collection_name,
            )

    def _load_collection_dimension(self, collection: Collection) -> int | None:
        result = collection.get(limit=1, include=["embeddings"])
        embeddings = _as_list(result.get("embeddings"))
        if not embeddings:
            return None
        return len(embeddings[0])

    def _metadata_to_chroma(self, metadata: ChunkMetadata) -> Metadata:
        result: Metadata = {
            "document_id": metadata.document_id,
            "filename": metadata.filename,
            "file_type": metadata.file_type,
            "domain": metadata.domain,
            "chunk_index": metadata.chunk_index,
            "created_at": metadata.created_at,
            "source": metadata.source,
            "tags": json.dumps(metadata.tags, ensure_ascii=True),
        }
        if metadata.owner_id is not None:
            result["owner_id"] = metadata.owner_id
        return result

    def _metadata_filter_to_chroma(self, metadata_filter: Metadata | None) -> Any:
        if not metadata_filter:
            return None
        if len(metadata_filter) == 1:
            return metadata_filter
        return {"$and": [{key: value} for key, value in metadata_filter.items()]}

    def _metadata_from_chroma(self, metadata: dict[str, Any]) -> ChunkMetadata:
        tags_value = metadata.get("tags", "[]")
        if isinstance(tags_value, str):
            tags = json.loads(tags_value)
        elif isinstance(tags_value, list):
            tags = tags_value
        else:
            tags = []
        return ChunkMetadata(
            document_id=str(metadata["document_id"]),
            filename=str(metadata["filename"]),
            file_type=str(metadata["file_type"]),
            domain=str(metadata["domain"]),
            chunk_index=int(metadata["chunk_index"]),
            created_at=str(metadata["created_at"]),
            source=str(metadata["source"]),
            tags=tags,
            owner_id=(
                str(metadata["owner_id"])
                if metadata.get("owner_id") is not None
                else None
            ),
        )

    def _chunks_from_get_result(self, result: dict[str, Any]) -> list[DocumentChunk]:
        ids = result.get("ids") or []
        documents = result.get("documents") or []
        embeddings = _as_list(result.get("embeddings"))
        metadatas = result.get("metadatas") or []
        chunks: list[DocumentChunk] = []
        for index, chunk_id in enumerate(ids):
            metadata = self._metadata_from_chroma(metadatas[index])
            chunks.append(
                DocumentChunk(
                    id=chunk_id,
                    document_id=metadata.document_id,
                    content=documents[index],
                    embedding=list(embeddings[index]),
                    metadata=metadata,
                )
            )
        return chunks

    def _search_results_from_query_result(
        self,
        result: dict[str, Any],
    ) -> list[VectorSearchResult]:
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        embeddings = _first_query_list(result.get("embeddings"))
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        search_results: list[VectorSearchResult] = []
        for index, chunk_id in enumerate(ids):
            metadata = self._metadata_from_chroma(metadatas[index])
            distance = float(distances[index])
            search_results.append(
                VectorSearchResult(
                    chunk=DocumentChunk(
                        id=chunk_id,
                        document_id=metadata.document_id,
                        content=documents[index],
                        embedding=list(embeddings[index]),
                        metadata=metadata,
                    ),
                    score=1.0 / (1.0 + distance),
                )
            )
        return search_results


def _batched(items: Sequence[T], batch_size: int) -> Iterable[Sequence[T]]:
    if batch_size < 1:
        raise VectorStoreError("batch_size must be greater than zero")
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)


def _first_query_list(value: Any) -> list[Any]:
    values = _as_list(value)
    if not values:
        return []
    first = values[0]
    if hasattr(first, "tolist"):
        return first.tolist()
    return list(first)
