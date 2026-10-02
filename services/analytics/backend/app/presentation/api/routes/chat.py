import json
import re
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from backend.app.application.rag.rag_service import RAGService
from backend.app.presentation.api.dependencies.providers import get_rag_service
from backend.app.presentation.api.schemas.knowledge import ChatStreamRequest


router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


@router.post(
    "/stream",
    summary="RAG 流式对话",
    description="以服务器发送事件增量返回回答和引用来源。",
)
async def stream_chat(
    request: ChatStreamRequest,
    rag_service: RAGService = Depends(get_rag_service),
) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        kwargs = {
            "collection_name": request.collection_name,
            "top_k": request.top_k,
            "metadata_filter": request.metadata_filter,
            "score_threshold": request.score_threshold,
            "retrieval_mode": request.retrieval_mode,
        }
        try:
            response = await rag_service.answer(request.message, **kwargs)
        except TypeError as exc:
            if "unexpected keyword argument 'retrieval_mode'" not in str(exc):
                raise
            kwargs.pop("retrieval_mode")
            response = await rag_service.answer(request.message, **kwargs)
        for chunk in _stream_chunks(response.answer):
            yield _sse("token", {"text": chunk})
        yield _sse(
            "citations",
            {
                "citations": [
                    citation.model_dump(mode="json")
                    for citation in response.citations
                ]
            },
        )
        yield _sse("done", {"metadata": response.metadata})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"


def _stream_chunks(text: str, *, target_size: int = 48) -> list[str]:
    parts = re.findall(r"\S+\s*|\s+", text)
    chunks: list[str] = []
    current = ""
    for part in parts:
        current += part
        if len(current) >= target_size:
            chunks.append(current)
            current = ""
    if current or not chunks:
        chunks.append(current)
    return chunks
