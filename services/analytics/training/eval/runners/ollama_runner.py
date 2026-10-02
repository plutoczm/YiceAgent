"""Ollama OpenAI 兼容接口推理后端（用于微调后模型及可选基座对比）。"""

from __future__ import annotations

import httpx


class OllamaRunner:
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        max_new_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature

    def generate(self, messages: list[dict], *, max_new_tokens: int | None = None) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_new_tokens or self.max_new_tokens,
            "stream": False,
        }
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        response.raise_for_status()
        body = response.json()
        return body["choices"][0]["message"]["content"].strip()
