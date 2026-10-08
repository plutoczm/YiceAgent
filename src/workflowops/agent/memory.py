"""Real BGE embedding recall, with user filters and authoritative version checks."""

import asyncio
import json
from uuid import NAMESPACE_URL, uuid5

from workflowops.config import OWNER, RUNTIME
from workflowops.model_store import ModelError


def model_view(row):
    return {k: row[k] for k in ("id", "key", "text", "version", "kind") if k in row}


class MemoryIndex:
    def __init__(self, store, vectors, inference):
        self.store, self.vectors, self.inference = store, vectors, inference
        self.locks = {}
        manifest = json.loads((RUNTIME / "models/manifest.json").read_text(encoding="utf-8"))
        self.revision = next(
            x["revision"] for x in manifest["models"] if x["model_id"] == "BAAI/bge-m3"
        )
        self.index_id = "memory-v1-" + self.revision
        self.collection = "wf_memory_" + self.revision[:24]

    async def ensure(self):
        await self.vectors.ensure(
            self.collection,
            {
                "owner": OWNER,
                "index_id": self.index_id,
                "embedding_model": "BAAI/bge-m3",
                "embedding_revision": self.revision,
            },
        )
        await self.vectors.api(
            "PUT",
            "/collections/" + self.collection + "/index",
            {"field_name": "subject", "field_schema": "keyword"},
        )

    async def update(self, actor, row):
        lock = self.locks.setdefault(row["id"], asyncio.Lock())
        async with lock:
            current = self.store.memory(actor, row["id"])
            if (
                current is None
                or current["version"] != row["version"]
                or current["status"] != row["status"]
            ):
                return {"status": "superseded_operation", "authority_saved": True}
            if current.get("index_status") == "ready" and current.get("index_id") == self.index_id:
                return {
                    "status": "ready",
                    "operation": "already_indexed",
                    "embedding_performed": False,
                }
            return await self._update(actor, row)

    async def _update(self, actor, row):
        try:
            await self.ensure()
            # Delete every version, filtered by the server's trusted owner and subject.
            await self.vectors.api(
                "POST",
                "/collections/" + self.collection + "/points/delete?wait=true",
                {
                    "filter": {
                        "must": self.filters(actor)
                        + [{"key": "memory_id", "match": {"value": row["id"]}}]
                    }
                },
            )
            if row["status"] == "active":
                embedded = await self.inference.embed([row["text"]])
                await self.vectors.put(
                    self.collection,
                    [
                        {
                            "id": str(
                                uuid5(
                                    NAMESPACE_URL,
                                    self.index_id + ":" + row["id"] + ":" + str(row["version"]),
                                )
                            ),
                            "vector": embedded["vectors"][0],
                            "payload": {
                                "owner": OWNER,
                                "index_id": self.index_id,
                                "tenant": actor.tenant_id,
                                "subject": actor.subject,
                                "memory_id": row["id"],
                                "version": row["version"],
                            },
                        }
                    ],
                )
            self.store.indexed(actor, row["id"], row["version"], "ready", self.index_id)
            return {
                "status": "ready",
                "model_id": "BAAI/bge-m3",
                "revision": self.revision,
                "embedding_performed": row["status"] == "active",
                "device": embedded["device"] if row["status"] == "active" else None,
            }
        except Exception:
            self.store.indexed(actor, row["id"], row["version"], "failed")
            # Authoritative state is already saved; never undo a deletion because index failed.
            return {"status": "failed", "authority_saved": True}

    def filters(self, actor):
        return [
            {"key": key, "match": {"value": value}}
            for key, value in (
                ("tenant", actor.tenant_id),
                ("subject", actor.subject),
                ("owner", OWNER),
                ("index_id", self.index_id),
            )
        ]

    async def search(self, actor, query):
        active = self.store.memories(actor)
        if not active:
            return []
        await self.ensure()
        embedded = await self.inference.embed([query])
        result = await self.vectors.api(
            "POST",
            "/collections/" + self.collection + "/points/query",
            {
                "query": embedded["vectors"][0],
                "limit": 20,
                "with_payload": True,
                "filter": {"must": self.filters(actor)},
            },
        )
        if result is None:
            raise ModelError("memory_index_missing")
        return [
            model_view(row)
            for row in self.store.resolve_memories(
                actor,
                [
                    (p["payload"].get("memory_id"), p["payload"].get("version"))
                    for p in result["points"]
                    if p.get("score", 0) >= 0.3
                ],
            )
        ]
