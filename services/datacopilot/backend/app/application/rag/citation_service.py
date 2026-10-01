from backend.app.application.rag.models import Citation, RetrievedChunk


class CitationService:
    def create_citations(self, retrieved_chunks: list[RetrievedChunk]) -> list[Citation]:
        citations: list[Citation] = []
        for item in retrieved_chunks:
            metadata = item.chunk.metadata
            citations.append(
                Citation(
                    document_name=metadata.filename,
                    chunk_reference=f"{metadata.source}#chunk-{metadata.chunk_index}",
                    similarity_score=item.score,
                    source_metadata={
                        "document_id": metadata.document_id,
                        "filename": metadata.filename,
                        "file_type": metadata.file_type,
                        "domain": metadata.domain,
                        "chunk_index": metadata.chunk_index,
                        "created_at": metadata.created_at,
                        "source": metadata.source,
                        "tags": metadata.tags,
                    },
                )
            )
        return citations
