"""Download only the two approved public BGE models into owned v2 storage."""

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORAGE = ROOT / ".runtime/v2/models"
MODELS = ("BAAI/bge-m3", "BAAI/bge-reranker-v2-m3")


def prepare():
    # Explicit anonymous access: do not read/use existing Hugging Face login.
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HOME"] = str(STORAGE / "hf-home")
    from huggingface_hub import HfApi, snapshot_download

    STORAGE.mkdir(parents=True, exist_ok=True)
    manifest_path = STORAGE / "manifest.json"
    previous = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    )
    if previous is not None and previous.get("owner") != "workflowops-v2":
        raise ValueError("model_storage_not_owned")
    manifest = {"owner": "workflowops-v2", "models": []}
    for model in MODELS:
        pinned = (
            next(
                (item["revision"] for item in previous["models"] if item["model_id"] == model), None
            )
            if previous
            else None
        )
        info = HfApi(token=False).model_info(
            model, revision=pinned, files_metadata=True, token=False
        )
        if not info.sha or not re.fullmatch(r"[a-f0-9]{40}", info.sha):
            raise ValueError("model_revision_invalid")
        available = {item.rfilename for item in info.siblings}
        weights = [name for name in available if "/" not in name and name.endswith(".safetensors")]
        if not weights:
            weights = [name for name in available if "/" not in name and name.endswith(".bin")]
        if not weights:
            raise ValueError("model_weights_missing")
        extras = {
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "sentencepiece.bpe.model",
            "vocab.txt",
            "sentence_bert_config.json",
            "modules.json",
            "1_Pooling/config.json",
            "colbert_linear.pt",
            "sparse_linear.pt",
            "README.md",
            "LICENSE",
            "model.safetensors.index.json",
            "pytorch_model.bin.index.json",
        }
        names = sorted(set(weights) | (extras & available))
        print(
            json.dumps(
                {
                    "event": "download_start",
                    "model_id": model,
                    "revision": info.sha,
                    "files": len(names),
                }
            ),
            flush=True,
        )
        for attempt in range(3):
            try:
                path = Path(
                    snapshot_download(
                        model,
                        revision=info.sha,
                        cache_dir=str(STORAGE / "cache"),
                        allow_patterns=names,
                        token=False,
                        max_workers=1,
                    )
                )
                break
            except Exception as error:
                print(
                    json.dumps(
                        {
                            "event": "download_retry",
                            "model_id": model,
                            "attempt": attempt + 1,
                            "error_type": type(error).__name__,
                        }
                    ),
                    flush=True,
                )
                if attempt == 2:
                    raise
                time.sleep(2)

        if not path.resolve().is_relative_to(STORAGE.resolve()):
            raise ValueError("model_snapshot_outside_owned_storage")
        hashes = {}
        for name in names:
            file = path / name
            if not file.is_file():
                raise ValueError("model_download_incomplete")
            digest = hashlib.sha256()
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                    digest.update(chunk)
            checksum = digest.hexdigest()
            remote = next(item for item in info.siblings if item.rfilename == name)
            remote_lfs = getattr(remote, "lfs", None)
            expected = getattr(remote_lfs, "sha256", None)
            if isinstance(remote_lfs, dict):
                expected = remote_lfs.get("sha256")
            if expected and checksum != expected:
                raise ValueError("model_remote_hash_mismatch")
            hashes[name] = {
                "sha256": checksum,
                "bytes": file.stat().st_size,
                "remote_sha256": expected,
            }
        manifest["models"].append(
            {"model_id": model, "revision": info.sha, "snapshot": str(path), "files": hashes}
        )
        print(
            json.dumps(
                {
                    "event": "download_verified",
                    "model_id": model,
                    "bytes": sum(f["bytes"] for f in hashes.values()),
                }
            ),
            flush=True,
        )
    temporary = manifest_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, manifest_path)
    print("Owned BGE snapshots prepared; no account login or paid calls", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved", action="store_true")
    args = parser.parse_args()
    if not args.approved:
        print("explicit_approval_required")
        return 1
    prepare()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
