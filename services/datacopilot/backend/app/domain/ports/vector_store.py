from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, Field

from backend.app.domain.entities.chunk import DocumentChunk


class VectorSearchResult(BaseModel):
    chunk: DocumentChunk
    score: float


class CollectionStats(BaseModel):
    name: str
    document_count: int = Field(ge=0)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)


class VectorStore(Protocol):
    def create_collection(
        self,
        name: str,
        metadata: dict[str, str | int | float | bool] | None = None,
    ) -> None:
        raise NotImplementedError

    def delete_collection(self, name: str) -> None:
        raise NotImplementedError

    def list_collections(self) -> list[str]:
        raise NotImplementedError

    def add_documents(
        self,
        collection_name: str,
        chunks: Sequence[DocumentChunk],
        *,
        batch_size: int = 100,
    ) -> None:
        raise NotImplementedError

    def update_documents(
        self,
        collection_name: str,
        chunks: Sequence[DocumentChunk],
        *,
        batch_size: int = 100,
    ) -> None:
        raise NotImplementedError

    def delete_documents(
        self,
        collection_name: str,
        ids: Sequence[str],
        *,
        batch_size: int = 100,
    ) -> None:
        raise NotImplementedError

    def list_documents(
        self,
        collection_name: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentChunk]:
        raise NotImplementedError

    def get_document(self, collection_name: str, document_id: str) -> DocumentChunk:
        raise NotImplementedError

    def similarity_search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        *,
        limit: int = 5,
        metadata_filter: dict[str, str | int | float | bool] | None = None,
    ) -> list[DocumentChunk]:
        raise NotImplementedError

    def similarity_search_with_scores(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        *,
        limit: int = 5,
        metadata_filter: dict[str, str | int | float | bool] | None = None,
    ) -> list[VectorSearchResult]:
        raise NotImplementedError

    def metadata_filter_search(
        self,
        collection_name: str,
        metadata_filter: dict[str, str | int | float | bool],
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentChunk]:
        raise NotImplementedError

    def collection_stats(self, collection_name: str) -> CollectionStats:
        raise NotImplementedError
