"""Offline CUDA and BGE smoke verification. No login, external calls or production benchmark."""

import argparse
import gc
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from workflowops.device import run_read_only_inference, safe_report  # noqa: E402

MODELS = ("BAAI/bge-m3", "BAAI/bge-reranker-v2-m3")
STORAGE = ROOT / ".runtime/v2/models"


def model_smoke(model_id, preference="auto"):
    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        HF_HOME=str(STORAGE / "hf-home"),
        HF_HUB_DISABLE_PROGRESS_BARS="1",
    )
    import numpy as np
    import torch

    torch.set_num_threads(2)
    manifest = json.loads((STORAGE / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("owner") != "workflowops-v2":
        raise ValueError("model_storage_not_owned")
    record = next(item for item in manifest["models"] if item["model_id"] == model_id)
    snapshot = Path(record["snapshot"]).resolve()
    if not snapshot.is_relative_to(STORAGE.resolve()):
        raise ValueError("model_snapshot_not_owned")

    def operation(device):
        started = perf_counter()
        if model_id == "BAAI/bge-m3":
            from FlagEmbedding import BGEM3FlagModel

            model = BGEM3FlagModel(
                str(snapshot),
                use_fp16=device.startswith("cuda"),
                devices=[device],
                batch_size=2,
                query_max_length=128,
                passage_max_length=128,
                trust_remote_code=False,
            )
            vectors = model.encode(
                ["订单已支付，如何申请退款？", "外卖制作中需要人工处理异常。"],
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False,
                max_length=128,
            )["dense_vecs"]
            assert vectors.shape == (2, 1024) and np.isfinite(vectors).all()
            actual_devices = sorted(
                {str(parameter.device) for parameter in model.model.parameters()}
            )
            assert actual_devices == [device]
            result = {
                "shape": list(vectors.shape),
                "actual_compute_devices": actual_devices,
                "norms": [float(v) for v in np.linalg.norm(vectors, axis=1)],
            }
            del model, vectors
        else:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(
                str(snapshot), local_files_only=True, trust_remote_code=False
            )
            model = AutoModelForSequenceClassification.from_pretrained(
                str(snapshot),
                local_files_only=True,
                trust_remote_code=False,
                dtype=torch.float16 if device.startswith("cuda") else torch.float32,
                device_map="cpu",
            )
            model = model.to(device)
            pairs = [
                ["订单退款怎么申请？", "申请整单退款需要查询订单并由客户确认。"],
                ["订单退款怎么申请？", "太阳系中木星是一颗气态巨行星。"],
            ]
            inputs = tokenizer(
                pairs, padding=True, truncation=True, max_length=128, return_tensors="pt"
            ).to(device)
            model.eval()
            with torch.inference_mode():
                scores = torch.sigmoid(model(**inputs).logits.reshape(-1).float()).cpu().tolist()
            actual_devices = sorted({str(parameter.device) for parameter in model.parameters()})
            assert actual_devices == [device]
            assert len(scores) == 2 and all(0 <= value <= 1 for value in scores)
            assert scores[0] > scores[1]
            result = {
                "scores": scores,
                "actual_compute_devices": actual_devices,
                "relevant_candidate_ranked_first": True,
            }
            del model, inputs
        gc.collect()
        if device.startswith("cuda"):
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
        result["elapsed_seconds"] = round(perf_counter() - started, 3)
        return result

    result, choice = run_read_only_inference(operation, preference)
    output = {
        "model_id": model_id,
        "revision": record["revision"],
        "selection": asdict(choice),
        "result": result,
        "validation": "real_inference_smoke_only",
    }
    path = ROOT / ".runtime/v2" / (model_id.split("/")[-1] + "-gpu-smoke.json")
    path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", action="store_true")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda:0"], default="auto")
    parser.add_argument("--model", choices=MODELS)
    args = parser.parse_args()
    if args.model:
        return model_smoke(args.model, args.device)
    if args.models:
        # Keep this coordinator free of Torch/CUDA: only one child owns a model/context.
        for model_id in MODELS:
            subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    "-B",
                    str(Path(__file__).resolve()),
                    "--model",
                    model_id,
                    "--device",
                    args.device,
                ],
                check=True,
                timeout=300,
            )
        return 0
    report = safe_report(args.device)
    (ROOT / ".runtime/v2/accelerator-evidence.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
