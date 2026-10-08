"""Main environment client; Torch and BGE stay in private inference workers."""

import math
import os
from typing import Any

from workflowops.compute import local_compute
from workflowops.config import ROOT, RUNTIME, Settings
from workflowops.contracts import RpcRequest
from workflowops.model_store import ModelError
from workflowops.rpc import JsonlWorker


class Inference:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.workers: dict[str, JsonlWorker] = {}

    async def worker(self, kind: str) -> JsonlWorker:
        if kind not in self.workers:
            # One owned Torch/CUDA process at a time on Windows; prevent resident-model competition.
            await self.close()
            env = os.environ.copy()
            for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
                env.pop(key, None)
            env.update(
                PYTHONPATH=str(ROOT / "src"),
                PYTHONDONTWRITEBYTECODE="1",
                PYTHONIOENCODING="utf-8",
                HF_HUB_OFFLINE="1",
            )
            obj = JsonlWorker(
                [
                    str(RUNTIME / "envs/inference/Scripts/python.exe"),
                    "-X",
                    "utf8",
                    "-B",
                    "-m",
                    "workflowops.inference_worker",
                    "--kind",
                    kind,
                    "--device",
                    self.settings.inference_device,
                ],
                env=env,
            )
            await obj.start()
            self.workers[kind] = obj
        return self.workers[kind]

    async def request(self, kind: str, method: str, payload: dict[str, Any]):
        async with local_compute():
            worker = await self.worker(kind)
            try:
                result = await worker.call(RpcRequest(method=method, payload=payload), timeout=600)
                expected = "BAAI/bge-m3" if kind == "embed" else "BAAI/bge-reranker-v2-m3"
                if not isinstance(result, dict) or result.get("model_id") != expected:
                    raise ModelError("inference_model_mismatch")
                self.workers.pop(kind, None)
                await worker.close()  # Release the owned compute job before the next Qwen/BGE job.
                return result
            except BaseException:
                self.workers.pop(kind, None)
                await worker.close()
                raise

    async def embed(self, texts: list[str]) -> dict[str, Any]:
        result = await self.request("embed", "embed", {"texts": texts})
        values = result.get("vectors")
        if (
            not isinstance(values, list)
            or len(values) != len(texts)
            or any(
                len(v) != 1024
                or any(type(x) not in {int, float} or not math.isfinite(x) for x in v)
                for v in values
            )
        ):
            raise ModelError("embedding_output_invalid")
        return result

    async def rerank(self, query: str, passages: list[str]) -> dict[str, Any]:
        result = await self.request("rerank", "rerank", {"query": query, "passages": passages})
        if len(result.get("scores", [])) != len(passages):
            raise ModelError("reranker_output_invalid")
        return result

    async def tokenize(self, texts: list[str]) -> list[int]:
        result = await self.request("embed", "tokenize", {"texts": texts})
        return result["token_counts"]

    async def close(self):
        for worker in self.workers.values():
            await worker.close()
        self.workers.clear()
