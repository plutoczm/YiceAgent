"""Preflight or run the future local-only Phase 7C lexical/Dense comparison."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import os
import re
from pathlib import Path

from pydantic import SecretStr

from app.core.config import Settings
from app.db.session import Database
from app.evaluation.dense_comparison_runner import (
    load_frozen_datasets,
    run_and_write_comparison,
)
from app.evaluation.dense_retriever import (
    DENSE_MODEL_DIMENSION,
    DENSE_MODEL_ID,
    FASTEMBED_VERSION,
    FastEmbedEmbeddingProvider,
)
from app.evaluation.retrieval_comparison import (
    FROZEN_COMPARISON_TOP_K,
    FROZEN_CORPUS_SHA256,
    FROZEN_K_VALUES,
)

ROOT = Path(__file__).resolve().parents[1]


def local_preflight(*, require_ready: bool) -> int:
    """Inspect frozen manifests and local cache paths without importing the model package."""
    try:
        datasets = load_frozen_datasets(ROOT)
    except Exception:
        print("FROZEN_DATASETS_VALID = NO")
        return 1
    model_available = FastEmbedEmbeddingProvider.model_available_local()
    try:
        installed = importlib.metadata.version("fastembed")
    except importlib.metadata.PackageNotFoundError:
        installed = "NOT_INSTALLED"
    print("FROZEN_DATASETS_VALID = YES")
    print(f"FROZEN_CORPUS_SHA256 = {FROZEN_CORPUS_SHA256}")
    print(f"FROZEN_DATASETS = {len(datasets)}")
    print(f"K_VALUES = {','.join(str(value) for value in FROZEN_K_VALUES)}")
    print(f"COMPARISON_TOP_K = {FROZEN_COMPARISON_TOP_K}")
    print(f"FASTEMBED_VERSION = {installed}")
    print(f"FASTEMBED_VERSION_MATCH = {'YES' if installed == FASTEMBED_VERSION else 'NO'}")
    print(f"MODEL_ID = {DENSE_MODEL_ID}")
    print(f"MODEL_DIMENSION = {DENSE_MODEL_DIMENSION}")
    print(f"MODEL_AVAILABLE_LOCAL = {'YES' if model_available else 'NO'}")
    print("NETWORK_REQUESTS = 0")
    print("PROVIDER_SECRETS_READ = NO")
    if require_ready and (installed != FASTEMBED_VERSION or not model_available):
        return 1
    return 0


def isolated_settings() -> Settings:
    """Reject ordinary databases and external addresses before connecting."""
    run_id = os.environ.get("AGENTOPSHUB_TEST_RUN_ID", "")
    name = os.environ.get("TEST_POSTGRES_DB", "")
    if (
        not re.fullmatch(r"[a-f0-9]{32}", run_id)
        or name != f"agentopshub_test_{run_id}"
        or os.environ.get("POSTGRES_DB") != name
        or os.environ.get("POSTGRES_HOST") != "127.0.0.1"
        or os.environ.get("POSTGRES_PORT") != os.environ.get("AGENTOPSHUB_TEST_PORT")
    ):
        raise ValueError(
            "Use python scripts/dev.py eval-retrieval-compare for an isolated database"
        )
    return Settings(
        _env_file=None,
        environment="test",
        database_host="127.0.0.1",
        database_port=int(os.environ["AGENTOPSHUB_TEST_PORT"]),
        database_name=name,
        database_user=os.environ["TEST_POSTGRES_USER"],
        database_password=SecretStr(os.environ["TEST_POSTGRES_PASSWORD"]),
    )


async def run() -> None:
    """Run only after the command has verified an existing local model cache."""
    if local_preflight(require_ready=True) != 0:
        raise RuntimeError("Pinned FastEmbed package and model must already be available locally.")
    database = Database(isolated_settings())
    try:
        await run_and_write_comparison(database, ROOT)
    finally:
        await database.close()


def main() -> int:
    """Choose metadata-only preflight or an explicitly invoked local model comparison."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--require-model-local", action="store_true")
    args = parser.parse_args()
    if args.preflight or args.require_model_local:
        return local_preflight(require_ready=args.require_model_local)
    try:
        asyncio.run(run())
    except Exception:
        print("Dense retrieval comparison failed; inspect isolated setup and model availability.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
