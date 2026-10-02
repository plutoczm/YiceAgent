from pydantic import BaseModel, Field


class RootResponse(BaseModel):
    service: str
    version: str
    status: str
    docs: str
    openapi: str


class LivenessResponse(BaseModel):
    service: str
    status: str


class ProviderHealthResponse(BaseModel):
    provider: str
    ok: bool
    api_key_configured: bool
    reachable: bool
    model_available: bool
    model: str | None = None
    message: str


class VectorStoreHealthResponse(BaseModel):
    status: str
    collection_name: str
    document_count: int = Field(ge=0)


class HealthResponse(BaseModel):
    service: str
    status: str
    environment: str
    llm_provider: ProviderHealthResponse
    vector_store: VectorStoreHealthResponse
