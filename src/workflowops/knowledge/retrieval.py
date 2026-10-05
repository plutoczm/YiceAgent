"""Actual BM25 + Dense + RRF + BGE model reranking, rehydrated from authoritative data."""

import hashlib
import math
import re
from collections import Counter
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from workflowops.config import OWNER
from workflowops.identity import Principal
from workflowops.inference import Inference
from workflowops.knowledge.corpus import blocks, chunks, load_corpus, manifest_hash
from workflowops.knowledge.store import Store
from workflowops.knowledge.vector import Vectors
from workflowops.model_store import ModelError


def terms(value: str) -> list[str]:
    # Chinese unigrams/bigrams and Latin words are explicit BM25 tokenization,
    # not hand-written business relevance scores.
    output = re.findall(r"[a-z0-9_]+", value.lower())
    for part in re.findall(r"[\u3400-\u9fff]+", value):
        output.extend(part)
        output.extend(part[i : i + 2] for i in range(len(part) - 1))
    return output


def bm25(query: str, rows: list[dict[str, Any]]) -> list[str]:
    bags = [Counter(terms(r["content"])) for r in rows]
    sizes = [sum(b.values()) for b in bags]
    average = sum(sizes) / max(1, len(sizes))
    scores = []
    for row, bag, size in zip(rows, bags, sizes, strict=True):
        score = 0.0
        for token in set(terms(query)):
            freq = bag[token]
            if freq:
                df = sum(1 for b in bags if b[token])
                idf = math.log(1 + (len(bags) - df + 0.5) / (df + 0.5))
                score += idf * freq * 2.5 / (freq + 1.5 * (0.25 + 0.75 * size / max(1, average)))
        if score > 0:
            scores.append((score, row["point_id"]))
    return [i for _, i in sorted(scores, key=lambda x: (-x[0], x[1]))[:20]]


def rrf(sparse: list[str], dense: list[str]) -> list[str]:
    values = {}
    for ranking in (sparse, dense):
        for rank, key in enumerate(ranking, 1):
            values[key] = values.get(key, 0) + 1 / (60 + rank)
    return sorted(values, key=lambda x: (-values[x], x))[:20]


class Retrieval:
    def __init__(self, store: Store, vectors: Vectors, inference: Inference):
        self.store, self.vectors, self.inference = store, vectors, inference

    async def import_corpus(self, name="demo-cn", *, activate=True):
        version, documents = load_corpus(name)
        # Revision is fixed by the owned downloaded manifest, not an invented vector space.
        import json

        from workflowops.config import RUNTIME

        manifest = json.loads((RUNTIME / "models/manifest.json").read_text(encoding="utf-8"))
        revision = next(m["revision"] for m in manifest["models"] if m["model_id"] == "BAAI/bge-m3")
        index_id = manifest_hash(version, documents, revision)
        collection = "wf_knowledge_" + index_id[:24]
        existing = self.store.index(index_id)
        metadata = {
            "owner": OWNER,
            "index_id": index_id,
            "embedding_model": "BAAI/bge-m3",
            "embedding_revision": revision,
        }
        await self.vectors.ensure(collection, metadata)
        if existing and existing["status"] == "ready":
            if await self.vectors.count(collection, index_id) != 2 * existing["chunk_count"]:
                raise ModelError("vector_count_mismatch")
            return {"status": "no_op", "index_id": index_id, "collection": collection}
        pieces = []
        doc_map = {d.id: d for d in documents}
        for document in documents:
            parts = blocks(document)
            counts = []
            for offset in range(0, len(parts), 64):
                counts.extend(await self.inference.tokenize(parts[offset : offset + 64]))
            pieces.extend(chunks(document, parts, counts))
        embeddings, devices, exact_counts = [], [], []
        for offset in range(0, len(pieces), 64):
            result = await self.inference.embed(
                [p["content"] for p in pieces[offset : offset + 64]]
            )
            if result["revision"] != revision:
                raise ModelError("embedding_space_mismatch")
            embeddings.extend(result["vectors"])
            exact_counts.extend(result["token_counts"])
            devices.append({"device": result["device"], "reason": result["selection_reason"]})
        rows, points = [], []
        for tenant in ("tenant-a", "tenant-b"):
            for piece, vector, count in zip(pieces, embeddings, exact_counts, strict=True):
                document = doc_map[piece["document_id"]]
                point_id = str(uuid5(NAMESPACE_URL, index_id + ":" + tenant + ":" + piece["id"]))
                row = {
                    "point_id": point_id,
                    "tenant": tenant,
                    "index_id": index_id,
                    "chunk_id": piece["id"],
                    "document_id": document.id,
                    "version": document.version,
                    "title": document.title,
                    "source": document.source,
                    "license": document.license,
                    "document_hash": document.sha256,
                    "chunk_hash": piece["sha256"],
                    "content": piece["content"],
                    "tokens": count,
                    "facts": document.facts,
                    "active": True,
                }
                rows.append(row)
                points.append(
                    {
                        "id": point_id,
                        "vector": vector,
                        "payload": {"tenant": tenant, "index_id": index_id, "owner": OWNER},
                    }
                )
        await self.vectors.put(collection, points)
        if await self.vectors.count(collection, index_id) != len(points):
            raise ModelError("vector_count_mismatch")
        self.store.create(
            {
                "id": index_id,
                "collection": collection,
                "corpus_version": version,
                "manifest_hash": index_id,
                "embedding_model": "BAAI/bge-m3",
                "embedding_revision": revision,
                "chunk_count": len(pieces),
            },
            rows,
            activate=activate,
        )
        return {
            "status": "ready",
            "index_id": index_id,
            "collection": collection,
            "chunks_per_tenant": len(pieces),
            "devices": devices,
        }

    async def search(
        self, actor: Principal, query: str, *, index_id: str | None = None, query_vector=None
    ):
        if not query.strip() or len(query) > 3000:
            raise ModelError("knowledge_query_invalid")
        info, rows = self.store.read(actor, index_id)
        if not rows:
            return {"status": "no_evidence", "evidence": []}
        if query_vector is None:
            embedded = await self.inference.embed([query])
            if embedded["revision"] != info["embedding_revision"]:
                raise ModelError("embedding_space_mismatch")
            query_vector = embedded["vectors"][0]
        matches = await self.vectors.query(
            info["collection"], query_vector, actor.tenant_id, info["id"]
        )
        by_id = {r["point_id"]: r for r in rows}
        dense = [str(p["id"]) for p in matches if str(p["id"]) in by_id]
        candidate_ids = rrf(bm25(query, rows), dense)
        if not candidate_ids:
            return {"status": "no_evidence", "evidence": []}
        candidates = [by_id[i] for i in candidate_ids]
        ranked = await self.inference.rerank(query, [r["content"] for r in candidates])
        import json

        from workflowops.config import ROOT

        profile = json.loads((ROOT / "config/retrieval-gate.json").read_text(encoding="utf-8"))
        if profile["revision"] != ranked["revision"] or not 0 < profile["minimum_score"] < 1:
            raise ModelError("reranker_gate_requires_calibration")
        scored = sorted(
            zip(ranked["scores"], candidates, strict=True), key=lambda p: (-p[0], p[1]["chunk_id"])
        )
        all_facts: dict[str, set[str]] = {}
        for row in rows:
            for key, value in row["facts"].items():
                all_facts.setdefault(key, set()).add(value)
        evidence, bytes_used, conflicts = [], 0, set()
        for score, row in scored[:5]:
            if score < profile["minimum_score"]:
                continue
            size = len(row["content"].encode()) + 200
            if bytes_used + size > 3600:
                continue  # Whole blocks only; conservative per-question evidence allocation.
            if hashlib.sha256(row["content"].encode()).hexdigest() != row["chunk_hash"]:
                raise ModelError("knowledge_content_corrupt")
            for key in row["facts"]:
                if len(all_facts.get(key, set())) > 1:
                    conflicts.add(key)
            evidence.append(
                {
                    "id": row["chunk_id"],
                    "document_id": row["document_id"],
                    "version": row["version"],
                    "source": row["source"],
                    "title": row["title"],
                    "content": row["content"],
                    "sha256": row["chunk_hash"],
                    "score": score,
                    "tokens": row["tokens"],
                }
            )
            bytes_used += size
        return {
            "status": "source_conflict" if conflicts else "grounded" if evidence else "no_evidence",
            "evidence": evidence,
            "conflict_keys": sorted(conflicts),
            "index_id": info["id"],
            "corpus_version": info["corpus_version"],
            "trace": {
                "dense_count": len(dense),
                "bm25_count": len(bm25(query, rows)),
                "reranked": len(candidates),
                "reranker_model": ranked["model_id"],
                "reranker_revision": ranked["revision"],
                "device": ranked["device"],
                "selection_reason": ranked["selection_reason"],
                "evidence_budget_source": "conservative_utf8_upper_estimate",
                "gate_threshold": profile["minimum_score"],
            },
            "top5_document_ids": [r["document_id"] for _, r in scored[:5]],
        }
