"""Application-owned ReAct scheduler; no agent framework or provider-specific protocol.

Handlers own model decisions and tool execution. This module owns legal transitions,
per-run state isolation, a dispatch ceiling, cooperative cancellation and trace metadata.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypedDict

from yice_agent.contracts import Message
from yice_agent.model_store import ModelError

ENGINE_ID = "yice-agent-react-v1"


class Phase(StrEnum):
    ANALYSE = "analyse"
    DECIDE = "decide"
    ACT = "act"
    FINISH = "finish"


@dataclass(frozen=True)
class LoopState:
    response: Message | None = None
    finished: bool = False
    result: dict[str, object] | None = None


class LoopUpdate(TypedDict, total=False):
    finished: bool
    response: Message
    result: dict[str, object]


@dataclass(frozen=True)
class Transition:
    step: int
    previous: Phase
    following: Phase
    reason: str


class RunContext(Protocol):
    def check(self) -> None: ...


class ReActLoop[C: RunContext]:
    def __init__(
        self,
        analyse: Callable[[LoopState, C], Awaitable[LoopUpdate]],
        decide: Callable[[LoopState, C], Awaitable[LoopUpdate]],
        act: Callable[[LoopState, C], Awaitable[LoopUpdate]],
        *,
        max_steps: int = 24,
    ):
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("invalid_loop_step_limit")
        self.max_steps = max_steps
        self._handlers = {Phase.ANALYSE: analyse, Phase.DECIDE: decide, Phase.ACT: act}

    @staticmethod
    def advance(phase: Phase, update: LoopUpdate) -> tuple[LoopState, Phase, str]:
        # Control outputs come from trusted handlers, never directly from raw model JSON.
        if not isinstance(update, dict) or set(update) - {"finished", "response", "result"}:
            raise ModelError("agent_transition_invalid")
        if "finished" not in update or type(update["finished"]) is not bool:
            raise ModelError("agent_transition_invalid")
        if phase not in {Phase.ANALYSE, Phase.DECIDE, Phase.ACT}:
            raise ModelError("agent_transition_invalid")
        if update["finished"]:
            result = update.get("result")
            if not isinstance(result, dict) or not result or "response" in update:
                raise ModelError("agent_transition_invalid")
            return LoopState(finished=True, result=result), Phase.FINISH, "verified_handler_result"
        if "result" in update:
            raise ModelError("agent_transition_invalid")
        if phase == Phase.ACT:
            if "response" in update:
                raise ModelError("agent_transition_invalid")
            # Observations are persisted by the executor. Consume this batch once;
            # the next decision must explicitly supply a new tool request.
            return LoopState(), Phase.DECIDE, "observations_recorded"
        if "response" in update:
            message = update["response"]
            if (
                not isinstance(message, Message)
                or message.role != "assistant"
                or not message.tool_calls
            ):
                raise ModelError("agent_transition_invalid")
            return LoopState(response=message), Phase.ACT, "validated_tool_request"
        if phase == Phase.ANALYSE:
            return LoopState(), Phase.DECIDE, "intent_ready"
        raise ModelError("agent_transition_invalid")

    async def run(
        self,
        context: C,
        *,
        observe: Callable[[Transition], None] | None = None,
    ) -> LoopState:
        # No mutable execution state is stored on this shared scheduler.
        phase, state = Phase.ANALYSE, LoopState()
        for step in range(1, self.max_steps + 1):
            context.check()
            await asyncio.sleep(0)  # Cancellation/fairness point even with immediate handlers.
            context.check()
            update = await self._handlers[phase](state, context)
            context.check()  # Never execute a returned action after its deadline.
            state, following, reason = self.advance(phase, update)
            if observe is not None:
                # A broken audit sink is fatal. It must not trigger tool/model replay.
                observe(Transition(step, phase, following, reason))
            context.check()
            if following == Phase.FINISH:
                return state
            phase = following
        raise ModelError("agent_step_budget_exceeded")
