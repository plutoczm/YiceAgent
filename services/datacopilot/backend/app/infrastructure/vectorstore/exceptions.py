class VectorStoreError(Exception):
    def __init__(
        self,
        message: str,
        *,
        collection_name: str | None = None,
        document_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.collection_name = collection_name
        self.document_id = document_id


class CollectionNotFoundError(VectorStoreError):
    pass


class DocumentNotFoundError(VectorStoreError):
    pass


class EmbeddingDimensionError(VectorStoreError):
    pass


class StorageError(VectorStoreError):
    pass
