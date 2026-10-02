from backend.app.application.rag.models import TextChunk


class ChunkingService:
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be greater than zero")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap must be greater than or equal to zero")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_text(self, text: str, *, document_id: str) -> list[TextChunk]:
        cleaned = clean_text(text)
        if not cleaned:
            return []

        chunks: list[TextChunk] = []
        start = 0
        while start < len(cleaned):
            end = min(start + self.chunk_size, len(cleaned))
            if end < len(cleaned):
                split_at = self._best_split(cleaned, start, end)
                if split_at > start:
                    end = split_at
            chunk_text = cleaned[start:end].strip()
            if chunk_text:
                chunks.append(
                    TextChunk(
                        document_id=document_id,
                        chunk_index=len(chunks),
                        text=chunk_text,
                    )
                )
            if end >= len(cleaned):
                break
            start = max(end - self.chunk_overlap, start + 1)
        return chunks

    def _best_split(self, text: str, start: int, end: int) -> int:
        candidates = [
            text.rfind("\n\n", start, end),
            text.rfind("\n", start, end),
            text.rfind(". ", start, end),
            text.rfind(" ", start, end),
        ]
        minimum = start + max(1, self.chunk_size // 3)
        valid = [candidate for candidate in candidates if candidate >= minimum]
        if valid:
            split = max(valid)
            if text[split : split + 2] == ". ":
                return split + 1
            return split
        return end


def clean_text(text: str) -> str:
    normalized_lines: list[str] = []
    previous_blank = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(raw_line.split())
        if not line:
            if not previous_blank:
                normalized_lines.append("")
            previous_blank = True
            continue
        normalized_lines.append(line)
        previous_blank = False
    return "\n".join(normalized_lines).strip()
