from pathlib import Path

import pytest

from backend.app.core.settings import Settings
from backend.app.infrastructure.embeddings import BGEM3EmbeddingProvider
from backend.app.infrastructure.embeddings.exceptions import EmbeddingProviderError


pytestmark = pytest.mark.anyio


class FakeBGEM3Model:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def encode(self, texts, **kwargs):
        self.calls.append({"texts": list(texts), **kwargs})
        return {"dense_vecs": [[1.0, 0.0, 0.5] for _ in texts]}


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        paths={
            "project_root": tmp_path,
            "data_dir": "data",
            "chromadb_dir": "data/chromadb",
            "uploads_dir": "data/uploads",
            "logs_dir": "data/logs",
            "cache_dir": "data/cache",
            "embeddings_dir": "data/embeddings",
            "temp_dir": "data/temp",
            "models_dir": "models",
        },
        embeddings={
            "default_model": "BAAI/bge-m3",
            "batch_size": 4,
            "max_length": 512,
            "device": "cpu",
            "use_fp16": True,
            "lazy_load": True,
        },
    )


async def test_bge_m3_provider_lazy_loads_real_model_contract(tmp_path: Path) -> None:
    fake_model = FakeBGEM3Model()
    factory_calls: list[dict] = []

    def factory(model_name: str, **kwargs):
        factory_calls.append({"model_name": model_name, **kwargs})
        return fake_model

    provider = BGEM3EmbeddingProvider(
        make_settings(tmp_path),
        model_factory=factory,
    )

    assert factory_calls == []
    results = await provider.embed_texts(["Spark AQE", "Hive 分区"])

    assert len(factory_calls) == 1
    assert factory_calls[0]["model_name"] == "BAAI/bge-m3"
    assert factory_calls[0]["devices"] == ["cpu"]
    assert factory_calls[0]["use_fp16"] is False
    assert fake_model.calls[0]["return_dense"] is True
    assert fake_model.calls[0]["return_sparse"] is False
    assert fake_model.calls[0]["return_colbert_vecs"] is False
    assert fake_model.calls[0]["batch_size"] == 4
    assert fake_model.calls[0]["max_length"] == 512
    assert results[0].embedding == [1.0, 0.0, 0.5]
    assert results[0].model == "BAAI/bge-m3"


async def test_bge_m3_provider_reuses_loaded_model(tmp_path: Path) -> None:
    fake_model = FakeBGEM3Model()
    calls = 0

    def factory(*args, **kwargs):
        nonlocal calls
        calls += 1
        return fake_model

    provider = BGEM3EmbeddingProvider(make_settings(tmp_path), model_factory=factory)

    await provider.embed_query("first")
    await provider.embed_query("second")

    assert calls == 1
    assert len(fake_model.calls) == 2


async def test_bge_m3_provider_wraps_model_load_errors(tmp_path: Path) -> None:
    def broken_factory(*args, **kwargs):
        raise OSError("model cache unavailable")

    provider = BGEM3EmbeddingProvider(
        make_settings(tmp_path),
        model_factory=broken_factory,
    )

    with pytest.raises(EmbeddingProviderError, match="Failed to load BGE-M3"):
        await provider.embed_query("test")
