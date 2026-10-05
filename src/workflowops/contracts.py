"""Closed cross-provider and worker contracts. No credentials in wire values."""

from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, strict=True)


class Channel(StrEnum):
    CHATGPT = "chatgpt"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"


class ToolDefinition(Closed):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=2000)
    parameters: dict[str, JsonValue]


class ToolCall(Closed):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, JsonValue]


class Message(Closed):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = Field(default=None, max_length=200000)
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=8)
    tool_call_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def role_fields(self) -> "Message":
        if self.tool_calls and self.role != "assistant":
            raise ValueError("tool_calls_require_assistant")
        if (self.role == "tool") != (self.tool_call_id is not None):
            raise ValueError("tool_result_id_required")
        if self.role != "assistant" and self.content is None:
            raise ValueError("content_required")
        if self.role == "assistant" and self.content is None and not self.tool_calls:
            raise ValueError("empty_assistant")
        return self


class GenerationRequest(Closed):
    messages: list[Message] = Field(min_length=1, max_length=512)
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=20)
    output_schema: dict[str, JsonValue] | None = None
    max_output_tokens: int = Field(default=1536, ge=1, le=1536)
    context_window: Literal[4096, 8192, 16384] = 8192
    timeout_seconds: int = Field(default=120, ge=1, le=600)

    @model_validator(mode="after")
    def complete_tool_groups(self) -> "GenerationRequest":
        expected: set[str] = set()
        seen: set[str] = set()
        for message in self.messages:
            if message.role == "tool":
                call_id = message.tool_call_id
                if call_id is None or call_id not in expected:
                    raise ValueError("orphan_tool_result")
                expected.remove(call_id)
                continue
            if expected:
                raise ValueError("incomplete_tool_group")
            if message.tool_calls:
                ids = [call.id for call in message.tool_calls]
                if len(set(ids)) != len(ids) or seen.intersection(ids):
                    raise ValueError("duplicate_tool_call_id")
                expected.update(ids)
                seen.update(ids)
        if expected:
            raise ValueError("incomplete_tool_group")
        return self


class Usage(Closed):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_cny: str | None = None
    source: Literal["provider", "unknown"] = "unknown"


class GenerationResult(Closed):
    message: Message
    model_id: str = Field(min_length=1, max_length=128)
    finish_reason: Literal["stop", "length", "tool_calls", "other"]
    usage: Usage = Field(default_factory=Usage)

    @model_validator(mode="after")
    def assistant_only(self) -> "GenerationResult":
        if self.message.role != "assistant":
            raise ValueError("provider_role_invalid")
        return self


class RpcRequest(Closed):
    request_id: UUID = Field(default_factory=uuid4)
    method: Literal[
        "health", "embed", "rerank", "tokenize", "models", "generate", "login", "cancel"
    ]
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class RpcError(Closed):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class RpcResponse(Closed):
    request_id: UUID
    result: JsonValue = None
    error: RpcError | None = None

    @model_validator(mode="after")
    def result_or_error(self) -> "RpcResponse":
        if self.error is not None and self.result is not None:
            raise ValueError("rpc_result_error_conflict")
        return self


class ModelCapabilities(Closed):
    channel: Channel
    model_id: str
    context_window: int = Field(ge=4096)
    tools: bool
    structured_output: bool
    ready: bool = False
    reason: str | None = None
