from __future__ import annotations

import dataclasses
import json
import pathlib

from app import retrieval

DEFAULT_KNOWLEDGE_PATH = pathlib.Path(__file__).with_name("data") / "knowledge_sources.json"


@dataclasses.dataclass(frozen=True, slots=True)
class KnowledgeSourceDocument:
    document_id: str
    version: str
    title: str
    text: str
    source_uri: str


class KnowledgeChunker:
    """Deterministic chunker with stable versioned chunk identifiers."""

    def __init__(self, *, max_chars: int = 1200, overlap_chars: int = 120) -> None:
        if max_chars < 200:
            raise ValueError("max_chars must be at least 200")
        if overlap_chars < 0 or overlap_chars >= max_chars:
            raise ValueError("overlap_chars must be between 0 and max_chars")
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    def chunk(self, source: KnowledgeSourceDocument) -> list[retrieval.RetrievalDocument]:
        text = source.text.strip()
        if not text:
            return []

        chunks: list[retrieval.RetrievalDocument] = []
        start = 0
        chunk_index = 0
        while start < len(text):
            end = min(start + self.max_chars, len(text))
            if end < len(text):
                end = self._prefer_boundary(text, start, end)
            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append(
                    retrieval.RetrievalDocument(
                        document_id=source.document_id,
                        title=source.title,
                        text=chunk_text,
                        source_uri=source.source_uri,
                        document_version=source.version,
                        chunk_id=f"{source.document_id}:{source.version}:{chunk_index:04d}",
                    )
                )
                chunk_index += 1
            if end >= len(text):
                break
            next_start = max(0, end - self.overlap_chars)
            if next_start <= start:
                next_start = end
            start = next_start
        return chunks

    def _prefer_boundary(self, text: str, start: int, end: int) -> int:
        minimum = start + self.max_chars // 2
        candidates = [
            text.rfind("\n\n", minimum, end),
            text.rfind("。", minimum, end),
            text.rfind(". ", minimum, end),
            text.rfind("；", minimum, end),
            text.rfind("; ", minimum, end),
        ]
        boundary = max(candidates)
        return boundary + 1 if boundary >= minimum else end


def load_knowledge_sources(
    path: str | pathlib.Path | None = None,
) -> list[KnowledgeSourceDocument]:
    source_path = pathlib.Path(path) if path is not None else DEFAULT_KNOWLEDGE_PATH
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("knowledge source file must contain a JSON list")

    sources: list[KnowledgeSourceDocument] = []
    seen_ids: set[str] = set()
    for raw in payload:
        if not isinstance(raw, dict):
            raise ValueError("each knowledge source must be a JSON object")
        source = _parse_source(raw)
        if source.document_id in seen_ids:
            raise ValueError(f"duplicate active knowledge document_id: {source.document_id}")
        seen_ids.add(source.document_id)
        sources.append(source)
    return sources


def build_retrieval_documents(
    sources: list[KnowledgeSourceDocument],
    *,
    chunker: KnowledgeChunker | None = None,
) -> list[retrieval.RetrievalDocument]:
    chunker = chunker or KnowledgeChunker()
    documents = [chunk for source in sources for chunk in chunker.chunk(source)]
    index_ids = [document.index_id for document in documents]
    if len(index_ids) != len(set(index_ids)):
        raise ValueError("knowledge chunk identifiers must be unique")
    return documents


def load_retrieval_documents(
    path: str | pathlib.Path | None = None,
) -> list[retrieval.RetrievalDocument]:
    return build_retrieval_documents(load_knowledge_sources(path))


def _parse_source(raw: dict[str, object]) -> KnowledgeSourceDocument:
    required = ("document_id", "version", "title", "text", "source_uri")
    values: dict[str, str] = {}
    for field in required:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"knowledge source field '{field}' must be a non-empty string")
        values[field] = value.strip()
    return KnowledgeSourceDocument(**values)
