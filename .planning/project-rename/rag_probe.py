"""Read-only real GPU retrieval after relocation; no LLM dispatch or vector writes."""

import asyncio
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
from yice_agent.config import Settings
from yice_agent.database import make_engine
from yice_agent.identity import PERSONAS
from yice_agent.inference import Inference
from yice_agent.knowledge.retrieval import Retrieval
from yice_agent.knowledge.store import Store
from yice_agent.knowledge.vector import Vectors


class ProbeInference(Inference):
    embed_metadata = None

    async def embed(self, texts):
        value = await super().embed(texts)
        self.embed_metadata = {k: value[k] for k in ("model_id", "revision", "device", "selection_reason")}
        self.embed_metadata["dimensions"] = len(value["vectors"][0])
        return value


async def main():
    settings = Settings.load()
    engine = make_engine(settings)
    inference = ProbeInference(settings)
    try:
        retrieval = Retrieval(Store(engine), Vectors(settings), inference)
        result = await retrieval.search(PERSONAS["customer-a1"], "电商订单发货后还能取消吗？")
        output = {"status": result["status"], "evidence_count": len(result["evidence"]), "evidence_ids": [r["id"] for r in result["evidence"]], "index_id": result["index_id"], "embedding": inference.embed_metadata, "trace": result["trace"], "llm_calls": 0, "vector_writes": 0}
        (root / ".runtime/v2/project-rename/rag-read-only.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(output, ensure_ascii=False))
        assert result["status"] == "grounded" and result["evidence"]
        assert inference.embed_metadata["dimensions"] == 1024
        assert result["trace"]["reranker_model"] == "BAAI/bge-reranker-v2-m3"
    finally:
        await inference.close()
        engine.dispose()


asyncio.run(main())
