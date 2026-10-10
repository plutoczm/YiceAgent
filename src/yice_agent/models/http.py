"""DeepSeek and local Ollama wire adapters. Only fixed provider targets are allowed."""

import asyncio
import json
import re
from typing import Any
from uuid import uuid4

import httpx

from yice_agent.config import Settings
from yice_agent.contracts import GenerationRequest, GenerationResult, Message, ToolCall, Usage
from yice_agent.model_store import ModelError


def validate_request(request: GenerationRequest) -> None:
    # Shared cross-provider token estimate, including definitions/schema; reserve 512 for protocol overhead.
    # Do not send recognizable credentials to any model, including local tools.
    secret = re.compile(
        r"(?i)\bsk-[a-z0-9_-]{16,}|(?:api[_ -]?key|password|bearer)\s*[:=]\s*[^\s]{12,}"
    )
    if any(m.content and secret.search(m.content) for m in request.messages):
        raise ModelError("credential_in_model_context")
    from yice_agent.tokens import estimate

    size = estimate(request)["input_tokens"]
    if size + request.max_output_tokens + 512 > request.context_window:
        raise ModelError("context_capacity_exceeded")


def tools(request: GenerationRequest) -> list[dict[str, Any]]:
    return [{"type": "function", "function": t.model_dump()} for t in request.tools]


def messages(request: GenerationRequest, *, ollama: bool) -> list[dict[str, Any]]:
    result = []
    for item in request.messages:
        wire: dict[str, Any] = {"role": item.role, "content": item.content or ""}
        if item.tool_call_id:
            if ollama:
                call = next(
                    c for m in request.messages for c in m.tool_calls if c.id == item.tool_call_id
                )
                wire["tool_name"] = call.name
            else:
                wire["tool_call_id"] = item.tool_call_id
        if item.tool_calls:
            wire["tool_calls"] = [
                {"function": {"name": c.name, "arguments": c.arguments}}
                if ollama
                else {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": json.dumps(c.arguments, ensure_ascii=False),
                    },
                }
                for c in item.tool_calls
            ]
        result.append(wire)
    if request.output_schema:
        result.insert(
            0,
            {
                "role": "system",
                "content": "Return only valid JSON matching this schema: "
                + json.dumps(request.output_schema, ensure_ascii=False),
            },
        )
    return result


def normalize(
    data: dict[str, Any], request: GenerationRequest, *, ollama: bool, model_id: str
) -> GenerationResult:
    if ollama:
        raw = data["message"]
        calls = raw.get("tool_calls") or []
        parsed = [
            ToolCall(
                id="ollama-" + uuid4().hex,
                name=c["function"]["name"],
                arguments=c["function"]["arguments"],
            )
            for c in calls
        ]
        inp, out = data.get("prompt_eval_count"), data.get("eval_count")
        finish = (
            "tool_calls" if parsed else "length" if data.get("done_reason") == "length" else "stop"
        )
    else:
        choice = data["choices"][0]
        raw = choice["message"]
        parsed = [
            ToolCall(
                id=c["id"],
                name=c["function"]["name"],
                arguments=json.loads(c["function"]["arguments"]),
            )
            for c in raw.get("tool_calls") or []
        ]
        usage = data.get("usage") or {}
        inp, out = usage.get("prompt_tokens"), usage.get("completion_tokens")
        finish = choice.get("finish_reason", "other")
    allowed = {t.name for t in request.tools}
    if any(c.name not in allowed for c in parsed):
        raise ModelError("unrequested_tool")
    if (
        inp is not None
        and (type(inp) is not int or inp < 0)
        or out is not None
        and (type(out) is not int or out < 0)
    ):
        raise ModelError("provider_usage_invalid")
    # thinking/reasoning_content never enter normalized messages or logs.
    return GenerationResult(
        message=Message(
            role="assistant",
            content=raw.get("content") or (None if parsed else ""),
            tool_calls=parsed,
        ),
        model_id=str(data.get("model") or model_id),
        finish_reason=finish if finish in {"stop", "length", "tool_calls"} else "other",
        usage=Usage(
            input_tokens=inp,
            output_tokens=out,
            source="provider" if inp is not None and out is not None else "unknown",
        ),
    )


class HttpAdapter:
    def __init__(
        self, settings: Settings, channel: str, *, transport: httpx.AsyncBaseTransport | None = None
    ):
        if channel not in {"qwen", "deepseek"}:
            raise ValueError("unknown_http_channel")
        self.settings, self.channel, self.transport = settings, channel, transport

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        validate_request(request)
        ollama = self.channel == "qwen"
        model_id = self.settings.ollama_model if ollama else self.settings.deepseek_model
        headers: dict[str, str] = {}
        body: dict[str, Any] = {
            "model": model_id,
            "messages": messages(request, ollama=ollama),
            "stream": False,
        }
        if request.tools:
            body["tools"] = tools(request)
        if ollama:
            target = "http://127.0.0.1:11434/api/chat"
            body.update(
                think=False,
                options={
                    "num_ctx": request.context_window,
                    "num_batch": 64,  # Lower prompt-processing buffers on an 8GB GPU.
                    "num_predict": request.max_output_tokens,
                    "temperature": 0,
                },
                keep_alive=0,  # Release this request's Qwen residency before the next BGE job.
            )
            if request.output_schema:
                body["format"] = request.output_schema
        else:
            if not self.settings.deepseek_key:
                raise ModelError("deepseek_key_required")
            target = "https://api.deepseek.com/chat/completions"
            headers["Authorization"] = "Bearer " + self.settings.deepseek_key.get_secret_value()
            body.update(
                thinking={"type": "disabled"}, max_tokens=request.max_output_tokens, temperature=0
            )
            if request.output_schema:
                body["response_format"] = {"type": "json_object"}
        try:
            async with asyncio.timeout(request.timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self.transport, trust_env=False, timeout=request.timeout_seconds
                ) as client:
                    response = await client.post(target, headers=headers, json=body)
                    if response.status_code in {401, 403}:
                        raise ModelError("provider_auth_failed")
                    if response.status_code in {402, 429}:
                        raise ModelError("provider_quota_unavailable")
                    if response.status_code >= 400:
                        raise ModelError("provider_request_failed")
                    if len(response.content) > 2 * 1024 * 1024:
                        raise ModelError("provider_response_too_large")
                    return normalize(response.json(), request, ollama=ollama, model_id=model_id)
        except (TimeoutError, httpx.TimeoutException):
            raise ModelError("provider_timeout") from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ModelError("provider_protocol_invalid") from None
