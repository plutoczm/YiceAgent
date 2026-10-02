from collections.abc import Sequence
from typing import Protocol

from backend.app.domain.entities.embedding import EmbeddingResult


class EmbeddingProvider(Protocol):
    async def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResult]:
        raise NotImplementedError

    async def embed_query(self, text: str) -> EmbeddingResult:
        raise NotImplementedError

    def provider_name(self) -> str:
        raise NotImplementedError

    def embedding_dimension(self) -> int:
        raise NotImplementedError
