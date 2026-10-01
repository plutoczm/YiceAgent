import hashlib
import math
import re
from collections.abc import Sequence

from backend.app.core.settings import Settings
from backend.app.domain.entities.embedding import EmbeddingResult


class HashingEmbeddingProvider:
    """轻量、离线、确定性的 hashing embedding，用于演示和测试。"""

    def __init__(self, settings: Settings, dimension: int = 1024) -> None:
        if dimension < 1:
            raise ValueError("dimension must be greater than zero")
        self.settings = settings
        self.model = settings.embeddings.default_model
        self.dimension = dimension

    def provider_name(self) -> str:
        return "hashing-local"

    def embedding_dimension(self) -> int:
        return self.dimension

    async def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResult]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> EmbeddingResult:
        return self._embed(text)

    def _embed(self, text: str) -> EmbeddingResult:
        tokens = _tokenize(text)
        vector = [0.0] * self.dimension
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return EmbeddingResult(
            text=text,
            embedding=vector,
            model=self.model,
            token_count=len(tokens),
        )


def _tokenize(text: str) -> list[str]:
    lowered = text.lower()
    words = re.findall(r"[a-z0-9_]+", lowered)
    chinese = re.findall(r"[\u4e00-\u9fff]", lowered)
    chinese_bigrams = [
        "".join(chinese[index : index + 2])
        for index in range(len(chinese) - 1)
    ]
    return words + chinese + chinese_bigrams
