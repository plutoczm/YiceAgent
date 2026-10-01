from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from backend.app.domain.entities.chunk import DocumentChunk
from backend.app.domain.entities.document import LoadedDocument
from backend.app.domain.ports.llm_provider import LLMUsage


class RetrievalMode(StrEnum):
    VECTOR = "vector"
    HYBRID = "hybrid"


class TextChunk(BaseModel):
    document_id: str
    chunk_index: int = Field(ge=0)
    text: str


class IngestionResult(BaseModel):
    document_id: str
    filename: str
    file_type: str
    domain: str
    stored_path: Path
    collection_name: str
    chunk_count: int = Field(ge=0)


class RetrievedChunk(BaseModel):
    chunk: DocumentChunk
    score: float
    dense_score: float = 0.0
    lexical_score: float = 0.0
    rerank_score: float = 0.0


class Citation(BaseModel):
    document_name: str
    chunk_reference: str
    similarity_score: float
    source_metadata: dict[str, str | int | float | bool | list[str]]


class RAGResponse(BaseModel):
    answer: str
    retrieved_chunks: list[DocumentChunk]
    citations: list[Citation]
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
    token_usage: LLMUsage = Field(default_factory=LLMUsage)
