from __future__ import annotations

import json
from typing import Any

import httpx


class ModelGatewayError(RuntimeError):
    pass


class OpenAICompatibleModel:
    """Small provider-neutral client for OpenAI-compatible chat-completions endpoints."""

    def __init__(self, *, base_url: str, model: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    def chat_json(self, *, system: str, user: str) -> dict[str, Any]:
        content = self._chat(system=system, user=user)
        try:
            return json.loads(self._strip_json_fence(content))
        except json.JSONDecodeError as exc:
            raise ModelGatewayError("invalid_json_model_output") from exc

    def chat_text(self, *, system: str, user: str) -> str:
        return self._chat(system=system, user=user)

    def _chat(self, *, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelGatewayError("model_request_failed") from exc
        if not isinstance(content, str) or not content.strip():
            raise ModelGatewayError("empty_model_output")
        return content.strip()

    @staticmethod
    def _strip_json_fence(content: str) -> str:
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        return stripped
