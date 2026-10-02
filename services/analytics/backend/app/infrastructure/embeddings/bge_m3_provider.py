import asyncio
from collections.abc import Callable, Sequence
from threading import Lock
from typing import Any

from backend.app.core.settings import Settings
from backend.app.domain.entities.embedding import EmbeddingResult
from backend.app.infrastructure.embeddings.exceptions import EmbeddingProviderError


class BGEM3EmbeddingProvider:
    """FlagEmbedding-backed BGE-M3 dense embedding provider."""

    def __init__(
        self,
        settings: Settings,
        *,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self.model_name = settings.embeddings.default_model
        self.batch_size = settings.embeddings.batch_size
        self.max_length = settings.embeddings.max_length
        self.device = _resolve_device(settings.embeddings.device)
        self.use_fp16 = settings.embeddings.use_fp16 and self.device.startswith("cuda")
        self.cache_dir = settings.paths.models_dir / "huggingface"
        self._model_factory = model_factory
        self._model: Any | None = None
        self._model_lock = Lock()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if not settings.embeddings.lazy_load:
            self._load_model()

    def provider_name(self) -> str:
        return "bge-m3"

    def embedding_dimension(self) -> int:
        return 1024

    async def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResult]:
        if not texts:
            return []
        values = list(texts)
        vectors = await asyncio.to_thread(self._encode, values)
        return [
            EmbeddingResult(
                text=text,
                embedding=vector,
                model=self.model_name,
                token_count=_estimated_token_count(text),
            )
            for text, vector in zip(values, vectors, strict=True)
        ]

    async def embed_query(self, text: str) -> EmbeddingResult:
        return (await self.embed_texts([text]))[0]

    def _encode(self, texts: list[str]) -> list[list[float]]:
        try:
            model = self._load_model()
            output = model.encode(
                texts,
                batch_size=self.batch_size,
                max_length=self.max_length,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False,
            )
            dense_vectors = output["dense_vecs"]
            if hasattr(dense_vectors, "tolist"):
                dense_vectors = dense_vectors.tolist()
            return [[float(value) for value in vector] for vector in dense_vectors]
        except EmbeddingProviderError:
            raise
        except Exception as exc:
            raise EmbeddingProviderError(
                f"BGE-M3 inference failed for model '{self.model_name}'"
            ) from exc

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            factory = self._model_factory
            if factory is None:
                from FlagEmbedding import BGEM3FlagModel

                factory = BGEM3FlagModel
            try:
                self._model = factory(
                    self.model_name,
                    use_fp16=self.use_fp16,
                    devices=[self.device],
                    cache_dir=str(self.cache_dir),
                )
            except Exception as exc:
                raise EmbeddingProviderError(
                    f"Failed to load BGE-M3 model '{self.model_name}'"
                ) from exc
            return self._model


def _resolve_device(configured: str) -> str:
    if configured != "auto":
        return configured
    try:
        import torch

        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _estimated_token_count(text: str) -> int:
    words = len(text.split())
    chinese_characters = sum("\u4e00" <= char <= "\u9fff" for char in text)
    return max(words, chinese_characters, 1)
