from pydantic import BaseModel, Field


class ChunkMetadata(BaseModel):
    document_id: str
    filename: str
    file_type: str
    domain: str
    chunk_index: int = Field(ge=0)
    created_at: str
    source: str
    tags: list[str] = Field(default_factory=list)
    owner_id: str | None = None


class DocumentChunk(BaseModel):
    id: str
    document_id: str
    content: str
    embedding: list[float]
    metadata: ChunkMetadata
