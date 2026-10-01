import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx


DEFAULT_BACKEND_URL = "http://backend:8000"


class BackendClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float = 60.0,
        http_client: Any | None = None,
        trust_env: bool = False,
    ) -> None:
        self.base_url = (base_url or os.getenv("BACKEND_URL") or DEFAULT_BACKEND_URL).rstrip("/")
        self._owns_client = http_client is None
        self.client = http_client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            trust_env=trust_env,
        )

    def health(self) -> dict[str, Any]:
        return self._json(self.client.get("/health"))

    def list_documents(self) -> dict[str, Any]:
        return self._json(self.client.get("/api/v1/knowledge/documents"))

    def upload_document(
        self,
        *,
        file_name: str,
        file_bytes: bytes,
        collection_name: str = "knowledge_base",
        domain: str = "general",
        tags: str = "",
    ) -> dict[str, Any]:
        response = self.client.post(
            "/api/v1/knowledge/documents",
            data={"collection_name": collection_name, "domain": domain, "tags": tags},
            files={"file": (Path(file_name).name, file_bytes)},
        )
        return self._json(response)

    def delete_document(self, document_id: str) -> dict[str, Any]:
        return self._json(self.client.delete(f"/api/v1/knowledge/documents/{document_id}"))

    def query_knowledge(
        self,
        question: str,
        *,
        collection_name: str = "knowledge_base",
        top_k: int = 5,
    ) -> dict[str, Any]:
        return self._json(
            self.client.post(
                "/api/v1/knowledge/query",
                json={
                    "question": question,
                    "collection_name": collection_name,
                    "top_k": top_k,
                },
            )
        )

    def stream_chat(
        self,
        message: str,
        *,
        collection_name: str = "knowledge_base",
        top_k: int = 5,
    ) -> Iterator[dict[str, Any]]:
        with self.client.stream(
            "POST",
            "/api/v1/chat/stream",
            json={
                "message": message,
                "collection_name": collection_name,
                "top_k": top_k,
            },
        ) as response:
            response.raise_for_status()
            yield from self._parse_sse_lines(response.iter_lines())

    def agent_chat(
        self,
        message: str,
        *,
        engine: str = "hive",
        schema_context: str | None = None,
        collection_name: str = "knowledge_base",
        top_k: int = 5,
        use_rag: bool = False,
        session_id: str | None = None,
        user_id: str | None = None,
        retrieval_mode: str = "hybrid",
    ) -> dict[str, Any]:
        payload = {
            "message": message,
            "engine": engine,
            "schema_context": schema_context,
            "collection_name": collection_name,
            "top_k": top_k,
            "use_rag": use_rag,
        }
        if session_id:
            payload["session_id"] = session_id
        if user_id:
            payload["user_id"] = user_id
        if retrieval_mode != "hybrid":
            payload["retrieval_mode"] = retrieval_mode
        return self._json(
            self.client.post(
                "/api/v1/agent/chat",
                json=payload,
            )
        )

    def stream_agent_chat(
        self,
        message: str,
        *,
        engine: str = "hive",
        schema_context: str | None = None,
        collection_name: str = "knowledge_base",
        top_k: int = 5,
        use_rag: bool = False,
        session_id: str | None = None,
        user_id: str | None = None,
        retrieval_mode: str = "hybrid",
    ) -> Iterator[dict[str, Any]]:
        payload = {
            "message": message,
            "engine": engine,
            "schema_context": schema_context,
            "collection_name": collection_name,
            "top_k": top_k,
            "use_rag": use_rag,
        }
        if session_id:
            payload["session_id"] = session_id
        if user_id:
            payload["user_id"] = user_id
        if retrieval_mode != "hybrid":
            payload["retrieval_mode"] = retrieval_mode
        with self.client.stream(
            "POST",
            "/api/v1/agent/chat/stream",
            json=payload,
        ) as response:
            response.raise_for_status()
            yield from self._parse_sse_lines(response.iter_lines())

    def text2sql(
        self,
        question: str,
        engine: str,
        schema_context: str,
        *,
        use_rag: bool = False,
    ) -> dict[str, Any]:
        return self._json(
            self.client.post(
                "/api/v1/text2sql",
                json={
                    "question": question,
                    "engine": engine,
                    "schema_context": schema_context,
                    "use_rag": use_rag,
                },
            )
        )

    def sql_review(
        self,
        sql: str,
        engine: str,
        *,
        include_llm_explanation: bool = True,
    ) -> dict[str, Any]:
        return self._json(
            self.client.post(
                "/api/v1/sql-review",
                json={
                    "sql": sql,
                    "engine": engine,
                    "include_llm_explanation": include_llm_explanation,
                },
            )
        )

    def warehouse_design(
        self,
        requirement: str,
        *,
        use_rag: bool = False,
        recommendation_language: str = "zh-CN",
    ) -> dict[str, Any]:
        return self._json(
            self.client.post(
                "/api/v1/warehouse-design",
                json={
                    "requirement": requirement,
                    "use_rag": use_rag,
                    "recommendation_language": recommendation_language,
                },
            )
        )

    def add_long_term_memory(self, user_id: str, content: str) -> dict[str, Any]:
        return self._json(
            self.client.post(
                "/api/v1/agent/memory/long-term",
                json={"user_id": user_id, "content": content},
            )
        )

    def list_long_term_memories(self, user_id: str) -> dict[str, Any]:
        return self._json(
            self.client.get(f"/api/v1/agent/memory/long-term/{user_id}")
        )

    def upsert_memory_rule(
        self,
        rule_id: str,
        *,
        content: str,
        user_id: str | None = None,
        scope: str = "user",
        priority: int = 100,
    ) -> dict[str, Any]:
        return self._json(
            self.client.put(
                f"/api/v1/agent/memory/rules/{rule_id}",
                json={
                    "content": content,
                    "user_id": user_id,
                    "scope": scope,
                    "priority": priority,
                },
            )
        )

    def get_agent_session_state(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        params = {"user_id": user_id} if user_id else None
        return self._json(
            self.client.get(
                f"/api/v1/agent/sessions/{session_id}/state",
                params=params,
            )
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _json(self, response) -> dict[str, Any]:
        response.raise_for_status()
        return response.json()

    def _parse_sse_lines(self, lines: Iterator[str]) -> Iterator[dict[str, Any]]:
        event: str | None = None
        for line in lines:
            if not line:
                event = None
                continue
            if line.startswith("event:"):
                event = line.removeprefix("event:").strip()
                continue
            if line.startswith("data:"):
                payload = line.removeprefix("data:").strip()
                yield {
                    "event": event or "message",
                    "data": json.loads(payload),
                }
