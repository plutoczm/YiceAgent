from pydantic import BaseModel, Field


class EmbeddingResult(BaseModel):
    text: str
    embedding: list[float]
    model: str
    token_count: int = Field(ge=0)
