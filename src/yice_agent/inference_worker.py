"""Offline BGE JSONL worker. A process loads exactly one embedding or reranking model."""

import argparse
import contextlib
import gc
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import Field

from yice_agent.config import RUNTIME
from yice_agent.contracts import Closed, RpcRequest, RpcResponse
from yice_agent.device import choose_device, probe_accelerator

MODEL_IDS = {"embed": "BAAI/bge-m3", "rerank": "BAAI/bge-reranker-v2-m3"}


class EmbedInput(Closed):
    texts: list[str] = Field(min_length=1, max_length=64)

    @classmethod
    def parse(cls, value):
        item = cls.model_validate(value)
        if any(not t.strip() or len(t) > 12000 for t in item.texts):
            raise ValueError("inference_text_invalid")
        return item


class RerankInput(Closed):
    query: str = Field(min_length=1, max_length=3000)
    passages: list[str] = Field(min_length=1, max_length=20)


class Runtime:
    def __init__(self, kind: str, preference: str):
        self.kind, self.preference = kind, preference
        self.model = None
        self.tokenizer = None
        self.device = None
        self.reason = None
        manifest = json.loads((RUNTIME / "models/manifest.json").read_text(encoding="utf-8"))
        if manifest["owner"] != "workflowops-v2":
            raise ValueError("model_storage_unowned")
        self.record = next(r for r in manifest["models"] if r["model_id"] == MODEL_IDS[kind])
        self.path = Path(self.record["snapshot"]).resolve()
        if not self.path.is_relative_to((RUNTIME / "models").resolve()):
            raise ValueError("model_storage_unowned")

    def load(self, force_cpu=False):
        import torch
        from transformers import AutoTokenizer

        if self.model is not None:
            return
        for name in ["config.json", "model.safetensors", "pytorch_model.bin"]:
            if name in self.record["files"]:
                h = hashlib.sha256()
                with (self.path / name).open("rb") as stream:
                    for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                        h.update(block)
                if h.hexdigest() != self.record["files"][name]["sha256"]:
                    raise ValueError("model_snapshot_hash_invalid")
        choice = choose_device("cpu" if force_cpu else self.preference, probe_accelerator())
        self.device, self.reason = (
            choice.device,
            "gpu_oom_cpu_retry" if force_cpu else choice.reason,
        )
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(self.path), local_files_only=True, trust_remote_code=False
        )
        if self.kind == "embed":
            from FlagEmbedding import BGEM3FlagModel

            self.model = BGEM3FlagModel(
                str(self.path), devices=self.device, use_fp16=self.device.startswith("cuda")
            )
        else:
            from transformers import AutoModelForSequenceClassification

            self.model = AutoModelForSequenceClassification.from_pretrained(
                str(self.path),
                local_files_only=True,
                trust_remote_code=False,
                dtype=torch.float16 if self.device.startswith("cuda") else torch.float32,
                device_map="cpu",
            ).to(self.device)
            self.model.eval()

    def clear(self):
        self.model = None
        self.tokenizer = None
        gc.collect()
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def perform(self, method: str, payload: dict[str, Any]):
        if method == "health":
            return {
                "kind": self.kind,
                "loaded": self.model is not None,
                "model_id": MODEL_IDS[self.kind],
                "revision": self.record["revision"],
            }
        if method not in {self.kind, "tokenize"} or method == "tokenize" and self.kind != "embed":
            raise ValueError("worker_method_invalid")
        try:
            return self.infer(method, payload)
        except Exception as error:
            import torch

            if not isinstance(error, torch.cuda.OutOfMemoryError) or not str(
                self.device
            ).startswith("cuda"):
                raise
            self.clear()
            self.load(force_cpu=True)
            return self.infer(method, payload)

    def infer(self, method, payload):
        self.load()
        import torch

        if method in {"embed", "tokenize"}:
            data = EmbedInput.parse(payload)
            counts = [len(self.tokenizer.encode(t, add_special_tokens=True)) for t in data.texts]
            if max(counts) > 1024:
                raise ValueError("embedding_input_capacity_exceeded")
            result = {"token_counts": counts}
            if method == "embed":
                values = self.model.encode(
                    data.texts,
                    batch_size=4,
                    max_length=1024,
                    return_dense=True,
                    return_sparse=False,
                    return_colbert_vecs=False,
                )["dense_vecs"].astype("float32")
                if values.shape != (len(data.texts), 1024) or not all(
                    math.isfinite(v) for row in values for v in row
                ):
                    raise ValueError("embedding_output_invalid")
                result.update(vectors=values.tolist(), dimensions=1024)
        else:
            data = RerankInput.model_validate(payload)
            if any(not t.strip() or len(t) > 12000 for t in data.passages):
                raise ValueError("reranker_input_invalid")
            pairs = [[data.query, t] for t in data.passages]
            encoded = self.tokenizer(pairs, padding=False, truncation=False)
            if any(len(ids) > 1024 for ids in encoded["input_ids"]):
                raise ValueError("reranker_input_capacity_exceeded")
            scores = []
            with torch.inference_mode():
                for offset in range(0, len(pairs), 2):
                    inputs = self.tokenizer(
                        pairs[offset : offset + 2],
                        padding=True,
                        truncation=False,
                        return_tensors="pt",
                    ).to(self.device)
                    scores.extend(
                        torch.sigmoid(self.model(**inputs).logits.reshape(-1).float())
                        .cpu()
                        .tolist()
                    )
            if not all(math.isfinite(v) and 0 <= v <= 1 for v in scores):
                raise ValueError("reranker_output_invalid")
            result = {"scores": scores}
        return {
            **result,
            "model_id": MODEL_IDS[self.kind],
            "revision": self.record["revision"],
            "device": self.device,
            "selection_reason": self.reason,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["embed", "rerank"], required=True)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda:0"], default="auto")
    args = parser.parse_args()
    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        TOKENIZERS_PARALLELISM="false",
        HF_HOME=str(RUNTIME / "models/hf-home"),
    )
    runtime = Runtime(args.kind, args.device)
    for line in sys.stdin.buffer:
        request = None
        try:
            if len(line) > 4 * 1024 * 1024:
                raise ValueError("request_too_large")
            request = RpcRequest.model_validate_json(line)
            with contextlib.redirect_stdout(sys.stderr):
                result = runtime.perform(request.method, request.payload)
            output = RpcResponse(request_id=request.request_id, result=result)
        except Exception as error:
            if request is None:
                break
            from yice_agent.contracts import RpcError

            # Whitelisted categories only; never model text, input or exception bodies.
            code = (
                str(error)
                if isinstance(error, ValueError)
                and str(error)
                in {
                    "embedding_input_capacity_exceeded",
                    "reranker_input_capacity_exceeded",
                    "worker_method_invalid",
                    "model_snapshot_hash_invalid",
                    "inference_text_invalid",
                }
                else "worker_inference_failed"
            )
            output = RpcResponse(request_id=request.request_id, error=RpcError(code=code))
        print(output.model_dump_json(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
