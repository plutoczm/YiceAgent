from pydantic import BaseModel, Field

from backend.app.application.rag.models import Citation, RetrievalMode
from backend.app.domain.ports.llm_provider import LLMUsage


class DocumentUploadResponse(BaseModel):
    document_id: str
    filename: str
    file_type: str
    domain: str
    collection_name: str
    chunk_count: int = Field(ge=0)


class DocumentSummary(BaseModel):
    document_id: str
    filename: str
    file_type: str
    domain: str
    collection_name: str
    chunk_count: int = Field(ge=0)


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]


class KnowledgeQueryRequest(BaseModel):
    question: str = Field(min_length=1, examples=["Spark AQE 有什么作用？"])
    collection_name: str = Field(default="knowledge_base")
    top_k: int = Field(default=5, ge=1, le=20)
    metadata_filter: dict[str, str | int | float | bool] | None = None
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID


class KnowledgeQueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    metadata: dict[str, str | int | float | bool]
    token_usage: LLMUsage


class ChatStreamRequest(BaseModel):
    message: str = Field(min_length=1, examples=["解释 Hive 分区裁剪的原理。"])
    collection_name: str = Field(default="knowledge_base")
    top_k: int = Field(default=5, ge=1, le=20)
    metadata_filter: dict[str, str | int | float | bool] | None = None
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID


class RuntimeConfigResponse(BaseModel):
    environment: str
    api_only: bool
    gpu_enabled: bool
    cpu_only: bool
    default_llm_provider: str
    embedding_model: str
    vector_store: str
    capabilities: dict[str, bool]
