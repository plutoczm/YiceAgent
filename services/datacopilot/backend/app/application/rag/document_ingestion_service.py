import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.application.rag.chunking_service import ChunkingService
from backend.app.application.rag.models import IngestionResult
from backend.app.domain.entities.chunk import ChunkMetadata, DocumentChunk
from backend.app.domain.ports.vector_store import VectorStore
from backend.app.domain.ports.document_loader import DocumentLoader
from backend.app.domain.ports.embedding_provider import EmbeddingProvider


class DocumentIngestionService:
    def __init__(
        self,
        *,
        loader_factory: DocumentLoader,
        chunking_service: ChunkingService,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        uploads_dir: Path,
    ) -> None:
        self.loader_factory = loader_factory
        self.chunking_service = chunking_service
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.uploads_dir = uploads_dir

    async def ingest_file(
        self,
        file_path: Path,
        *,
        collection_name: str,
        domain: str,
        tags: list[str] | None = None,
    ) -> IngestionResult:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        stored_path = self._store_original_file(file_path)
        loaded = self.loader_factory.load(stored_path, domain=domain, tags=tags or [])
        text_chunks = self.chunking_service.chunk_text(
            loaded.content,
            document_id=loaded.document_id,
        )
        embeddings = await self.embedding_provider.embed_texts(
            [chunk.text for chunk in text_chunks]
        )
        created_at = datetime.now(timezone.utc).isoformat()
        chunks = [
            DocumentChunk(
                id=f"{loaded.document_id}:{text_chunk.chunk_index}",
                document_id=loaded.document_id,
                content=text_chunk.text,
                embedding=embedding.embedding,
                metadata=ChunkMetadata(
                    document_id=loaded.document_id,
                    filename=loaded.filename,
                    file_type=loaded.file_type,
                    domain=loaded.domain,
                    chunk_index=text_chunk.chunk_index,
                    created_at=created_at,
                    source=loaded.filename,
                    tags=loaded.tags,
                ),
            )
            for text_chunk, embedding in zip(text_chunks, embeddings, strict=True)
        ]
        self.vector_store.create_collection(collection_name)
        self.vector_store.add_documents(collection_name, chunks)
        return IngestionResult(
            document_id=loaded.document_id,
            filename=loaded.filename,
            file_type=loaded.file_type,
            domain=loaded.domain,
            stored_path=stored_path,
            collection_name=collection_name,
            chunk_count=len(chunks),
        )

    def _store_original_file(self, file_path: Path) -> Path:
        suffix = file_path.suffix.lower()
        destination = self.uploads_dir / file_path.name
        if destination.exists():
            destination = self.uploads_dir / f"{file_path.stem}-{uuid4().hex[:8]}{suffix}"
        shutil.copy2(file_path, destination)
        return destination
