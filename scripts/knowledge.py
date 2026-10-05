"""Prepare owned knowledge/model schemas and execute real BGE/Qdrant retrieval."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from workflowops.config import RUNTIME, Settings  # noqa: E402
from workflowops.database import make_engine  # noqa: E402
from workflowops.identity import PERSONAS  # noqa: E402
from workflowops.inference import Inference  # noqa: E402
from workflowops.knowledge.retrieval import Retrieval  # noqa: E402
from workflowops.knowledge.store import Store  # noqa: E402
from workflowops.knowledge.store import prepare as prepare_knowledge  # noqa: E402
from workflowops.knowledge.vector import Vectors  # noqa: E402
from workflowops.model_store import ModelStore  # noqa: E402
from workflowops.model_store import prepare as prepare_models  # noqa: E402
from workflowops.models.gateway import Gateway  # noqa: E402


async def run(args):
    settings = Settings.load()
    engine = make_engine(settings)
    inference = Inference(settings)
    retrieval = Retrieval(Store(engine), Vectors(settings), inference)
    try:
        if args.command in {"prepare", "import-demo", "benchmark"} and not args.approved:
            raise ValueError("explicit_approval_required")
        if args.command == "prepare":
            prepare_models(engine)
            prepare_knowledge(engine)
            result = {
                "owned_schemas": ["workflowops_models", "workflowops_knowledge"],
                "status": "ready",
            }
        elif args.command == "import-demo":
            result = await retrieval.import_corpus()
        elif args.command == "search":
            result = await retrieval.search(PERSONAS["customer-a1"], args.query)
        elif args.command == "ask":
            from workflowops.knowledge.answer import answer

            retrieved = await retrieval.search(PERSONAS["customer-a1"], args.query)
            gateway = Gateway(settings, ModelStore(engine), inference)
            try:
                result = await answer(
                    gateway, args.channel, "tenant-a:customer-a1", args.query, retrieved
                )
            finally:
                await gateway.close()
        else:
            fixtures = json.loads(
                (ROOT / "data/knowledge/queries-40.json").read_text(encoding="utf-8")
            )
            test_path = ROOT / "data/knowledge/queries-40.json"
            import hashlib

            normal = await retrieval.import_corpus()
            challenge = await retrieval.import_corpus("challenge-cn", activate=False)
            embeddings = await inference.embed([q["query"] for q in fixtures["queries"]])
            cases = []
            for q, vector in zip(fixtures["queries"], embeddings["vectors"], strict=True):
                indexed = normal if q["corpus"] == "demo-cn" else challenge
                result = await retrieval.search(
                    PERSONAS["customer-a1"],
                    q["query"],
                    index_id=indexed["index_id"],
                    query_vector=vector,
                )
                hit = (
                    bool(set(q["expected_documents"]) & set(result["top5_document_ids"]))
                    if q["kind"] == "answer"
                    else None
                )
                cases.append(
                    {
                        "id": q["id"],
                        "kind": q["kind"],
                        "query": q["query"],
                        "hit_at5": hit,
                        "status": result["status"],
                        "documents": result["top5_document_ids"],
                        "trace": result["trace"],
                    }
                )
                print(
                    json.dumps(
                        {
                            "progress": len(cases),
                            "id": q["id"],
                            "status": result["status"],
                            "hit_at5": hit,
                        }
                    ),
                    flush=True,
                )
            valid = [c for c in cases if c["kind"] == "answer"]
            hitrate = sum(c["hit_at5"] for c in valid) / len(valid)
            result = {
                "fixture_sha256": hashlib.sha256(test_path.read_bytes()).hexdigest(),
                "count": len(cases),
                "answer_denominator": len(valid),
                "hitrate_at5": hitrate,
                "target_met": hitrate >= 0.85,
                "cases": cases,
                "embedding_revision": embeddings["revision"],
                "embedding_device": embeddings["device"],
                "normal_index": normal,
                "challenge_index": challenge,
                "no_answer_correct": sum(
                    c["status"] == "no_evidence" for c in cases if c["kind"] == "no_answer"
                ),
                "conflict_correct": sum(
                    c["status"] == "source_conflict" for c in cases if c["kind"] == "conflict"
                ),
            }
            output = RUNTIME / "rag-40-result.json"
            output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    finally:
        await inference.close()
        engine.dispose()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "import-demo", "search", "ask", "benchmark"])
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--query", default="电商已发货后能取消吗？")
    parser.add_argument("--channel", choices=["qwen", "chatgpt", "deepseek"], default="qwen")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except Exception as exc:
        # Categorized failures only; never output user text/provider/credential exception bodies.
        import re

        from workflowops.model_store import ModelError
        from workflowops.rpc import WorkerError

        code = (
            str(exc)
            if isinstance(exc, (ModelError, WorkerError))
            and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(exc))
            else "knowledge_failed"
        )
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "code": code}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
