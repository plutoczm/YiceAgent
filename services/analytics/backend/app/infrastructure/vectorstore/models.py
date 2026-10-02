from pydantic import BaseModel, Field


class ChromaDocumentRecord(BaseModel):
    id: str
    document: str
    embedding: list[float]
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
