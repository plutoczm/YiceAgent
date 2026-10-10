"""Fixed local Qdrant REST targets, collection ownership and server-injected filters."""

import httpx

from yice_agent.config import OWNER, Settings
from yice_agent.model_store import ModelError


class Vectors:
    def __init__(self, settings: Settings):
        self.key = settings.qdrant_key

    async def api(self, method, path, value=None):
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:6335", trust_env=False, timeout=60
        ) as client:
            result = await client.request(
                method, path, headers={"api-key": self.key.get_secret_value()}, json=value
            )
            if result.status_code == 404:
                return None
            if result.status_code >= 400:
                raise ModelError("vector_service_failed")
            return result.json()["result"]

    async def ensure(self, name, metadata):
        info = await self.api("GET", "/collections/" + name)
        if info is None:
            await self.api(
                "PUT",
                "/collections/" + name,
                {"vectors": {"size": 1024, "distance": "Cosine"}, "metadata": metadata},
            )
            for field in ("tenant", "index_id", "owner"):
                await self.api(
                    "PUT",
                    "/collections/" + name + "/index",
                    {
                        "field_name": field,
                        "field_schema": {
                            "type": "keyword",
                            **({"is_tenant": True} if field == "tenant" else {}),
                        },
                    },
                )
            info = await self.api("GET", "/collections/" + name)
        actual = info["config"]
        if (
            actual.get("metadata") != metadata
            or actual["params"]["vectors"]["size"] != 1024
            or actual["params"]["vectors"]["distance"] != "Cosine"
        ):
            raise ModelError("vector_collection_unowned")

    async def put(self, name, points):
        for offset in range(0, len(points), 32):
            await self.api(
                "PUT",
                "/collections/" + name + "/points?wait=true",
                {"points": points[offset : offset + 32]},
            )

    async def count(self, name, index_id):
        result = await self.api(
            "POST",
            "/collections/" + name + "/points/count",
            {
                "exact": True,
                "filter": {
                    "must": [
                        {"key": "index_id", "match": {"value": index_id}},
                        {"key": "owner", "match": {"value": OWNER}},
                    ]
                },
            },
        )
        if result is None:
            raise ModelError("vector_index_missing")
        return result["count"]

    async def query(self, name, vector, tenant, index_id):
        result = await self.api(
            "POST",
            "/collections/" + name + "/points/query",
            {
                "query": vector,
                "limit": 20,
                "with_payload": True,
                "filter": {
                    "must": [
                        {"key": "tenant", "match": {"value": tenant}},
                        {"key": "index_id", "match": {"value": index_id}},
                        {"key": "owner", "match": {"value": OWNER}},
                    ]
                },
            },
        )
        if result is None:
            raise ModelError("vector_index_missing")
        return result["points"]
