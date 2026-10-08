"""Closed user inputs and model outputs; no identity or execution authority fields."""

from typing import Literal

from pydantic import Field, JsonValue

from workflowops.business.contracts import Command, ObjectId
from workflowops.contracts import Closed
from workflowops.models.gateway import Intent

ChannelName = Literal["qwen", "deepseek", "chatgpt"]


class ConversationCreate(Closed):
    title: str = Field(default="新会话", min_length=1, max_length=100)
    channel: ChannelName = "qwen"
    context_window: Literal[4096, 8192, 16384] = 8192


class ConversationConfig(Closed):
    channel: ChannelName
    context_window: Literal[4096, 8192, 16384] = 8192


class TurnRequest(Command):
    text: str = Field(min_length=1, max_length=20000)


class SourceQuote(Closed):
    message_id: str = Field(min_length=1, max_length=36)
    quote: str = Field(min_length=1, max_length=20000)


class CandidateInput(Closed):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    kind: Literal["preference", "stable_fact"]
    quote: str = Field(min_length=1, max_length=500)


class Analysis(Intent):
    candidates: list[CandidateInput] = Field(default_factory=list, max_length=3)
    correction: bool = False


class SummaryOutput(Closed):
    goals: list[SourceQuote] = Field(max_length=12)
    constraints: list[SourceQuote] = Field(max_length=24)
    corrections: list[SourceQuote] = Field(max_length=12)
    confirmed: list[SourceQuote] = Field(max_length=12)
    unresolved: list[SourceQuote] = Field(max_length=12)


class FinalOutput(Closed):
    decision: Literal["respond", "knowledge", "clarify"]
    reply: str = Field(default="", max_length=1200)
    quotes: list[dict[str, str]] = Field(default_factory=list, max_length=3)
    source_quotes: list[SourceQuote] = Field(default_factory=list, max_length=5)


class IntentChoice(Closed):
    intent: Literal[
        "knowledge",
        "order_lookup",
        "place_order",
        "pay",
        "cancel",
        "refund",
        "ticket",
        "memory",
        "clarify",
    ]


class RecallSelection(Closed):
    source_ids: list[str] = Field(max_length=5)


class PlannedTool(Closed):
    name: Literal[
        "search_knowledge",
        "read_memories",
        "list_products",
        "read_order",
        "list_tickets",
        "propose_action",
        "read_result",
    ]
    arguments: dict[str, JsonValue]


class StepOutput(Closed):
    action: Literal["tool", "final"]
    calls: list[PlannedTool] = Field(default_factory=list, max_length=4)
    final: FinalOutput | None = None


class MemoryConfirm(Command):
    expected_version: int = Field(ge=1)
    confirm: bool


class MemoryEdit(Command):
    expected_version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=500)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class MemoryDelete(Command):
    expected_version: int = Field(ge=1)


class ActionConfirmation(Command):
    action_id: ObjectId
    expected_version: int = Field(ge=1)
    confirm: bool


class Empty(Closed):
    pass
