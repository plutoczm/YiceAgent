from pathlib import Path

from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    document_id: str
    filename: str
    file_type: str
    domain: str
    created_at: str
    source: str
    tags: list[str] = Field(default_factory=list)


class Document(BaseModel):
    id: str
    content: str
    metadata: DocumentMetadata


class LoadedDocument(BaseModel):
    document_id: str
    filename: str
    file_type: str
    domain: str
    source_path: Path
    stored_path: Path | None = None
    content: str
    tags: list[str] = Field(default_factory=list)
