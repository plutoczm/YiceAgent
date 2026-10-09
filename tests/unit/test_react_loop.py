"""Handwritten scheduler control tests; handlers/model decisions are explicit test doubles."""

import asyncio
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, dataclass, field
from pathlib import Path

import pytest

from workflowops.agent.loop import LoopState, Phase, ReActLoop, Transition
from workflowops.contracts import Message, ToolCall
from workflowops.model_store import ModelError


@dataclass
class Context:
    name: str = "one"
    expired: bool = False
    phases: list[str] = field(default_factory=list)
    observations: int = 0

    def check(self):
        if self.expired:
            raise ModelError("run_deadline_exceeded")


def tool_message(name="lookup"):
    return Message(role="assistant", tool_calls=[ToolCall(id="call", name=name, arguments={})])


async def analyse(state, context):
    context.phases.append("analyse")
    return {"finished": False}


async def decide(state, context):
    context.phases.append("decide")
    assert state.response is None  # A consumed batch cannot be carried into the next decision.
    if context.observations:
        return {"finished": True, "result": {"answer": context.name}}
    return {"finished": False, "response": tool_message()}


async def act(state, context):
    context.phases.append("act")
    assert state.response.tool_calls[0].name == "lookup"
    context.observations += 1
    return {"finished": False}


def test_decision_action_observation_final_and_metadata_order():
    context, trace = Context(), []
    result = asyncio.run(ReActLoop(analyse, decide, act).run(context, observe=trace.append))
    assert context.phases == ["analyse", "decide", "act", "decide"]
    assert result.finished and result.result == {"answer": "one"}
    assert [(t.previous, t.following) for t in trace] == [
        (Phase.ANALYSE, Phase.DECIDE),
        (Phase.DECIDE, Phase.ACT),
        (Phase.ACT, Phase.DECIDE),
        (Phase.DECIDE, Phase.FINISH),
    ]
    assert [t.step for t in trace] == [1, 2, 3, 4]


def test_terminal_analysis_does_not_call_model_or_tools_after_clarification():
    async def clarify(state, context):
        return {"finished": True, "result": {"status": "clarification"}}

    context = Context()
    result = asyncio.run(ReActLoop(clarify, decide, act).run(context))
    assert result.result == {"status": "clarification"}
    assert context.phases == [] and context.observations == 0


def test_prevalidated_intent_action_runs_before_optional_model_decision():
    async def initial(state, context):
        context.phases.append("analyse")
        return {"finished": False, "response": tool_message()}

    context = Context()
    asyncio.run(ReActLoop(initial, decide, act).run(context))
    assert context.phases == ["analyse", "act", "decide"]


def test_pending_confirmation_finishes_without_another_decision_or_write():
    async def draft(state, context):
        context.observations += 1
        return {"finished": True, "result": {"status": "awaiting_confirmation"}}

    context = Context()
    result = asyncio.run(ReActLoop(analyse, decide, draft).run(context))
    assert result.result == {"status": "awaiting_confirmation"}
    assert context.phases == ["analyse", "decide"] and context.observations == 1


@pytest.mark.parametrize(
    ("phase", "patch"),
    [
        (Phase.ANALYSE, {"finished": 1}),
        (Phase.ANALYSE, {"finished": False, "next_phase": "act"}),
        (Phase.DECIDE, {"finished": False}),
        (Phase.DECIDE, {"finished": False, "response": Message(role="assistant", content="tool")}),
        (Phase.DECIDE, {"finished": False, "response": Message(role="user", content="tool")}),
        (Phase.ACT, {"finished": False, "response": tool_message()}),
        (Phase.ACT, {"finished": True, "result": {}}),
        (Phase.DECIDE, {"finished": True, "result": {"ok": True}, "response": tool_message()}),
        (Phase.ANALYSE, {"finished": False, "result": {"ok": True}}),
        (Phase.FINISH, {"finished": False}),
    ],
)
def test_ambiguous_or_illegal_control_output_cannot_dispatch_tools(phase, patch):
    with pytest.raises(ModelError, match="agent_transition_invalid"):
        ReActLoop.advance(phase, patch)


def test_dispatch_cap_stops_a_no_progress_handler_loop_before_an_extra_action():
    async def repeated(state, context):
        context.phases.append("decide")
        return {"finished": False, "response": tool_message()}

    context = Context()
    with pytest.raises(ModelError, match="agent_step_budget_exceeded"):
        asyncio.run(ReActLoop(analyse, repeated, act, max_steps=4).run(context))
    assert context.phases == ["analyse", "decide", "act", "decide"]
    assert context.observations == 1  # Pending second batch was not executed.


@pytest.mark.parametrize("limit", [0, -1, True])
def test_invalid_dispatch_limits_are_rejected(limit):
    with pytest.raises(ValueError, match="invalid_loop_step_limit"):
        ReActLoop(analyse, decide, act, max_steps=limit)


def test_expiry_after_model_await_prevents_the_returned_tool_from_executing():
    async def late_decision(state, context):
        await asyncio.sleep(0)
        context.expired = True
        return {"finished": False, "response": tool_message()}

    context = Context()
    with pytest.raises(ModelError, match="run_deadline_exceeded"):
        asyncio.run(ReActLoop(analyse, late_decision, act).run(context))
    assert context.observations == 0


def test_cancel_while_model_waits_propagates_without_running_action():
    async def scenario():
        entered = asyncio.Event()

        async def waiting(state, context):
            entered.set()
            await asyncio.Event().wait()

        context = Context()
        task = asyncio.create_task(ReActLoop(analyse, waiting, act).run(context))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert context.observations == 0

    asyncio.run(scenario())


def test_unknown_write_outcome_propagates_once_without_retrying_callback():
    class OutcomeUnknown(RuntimeError):
        pass

    async def unknown(state, context):
        context.observations += 1
        raise OutcomeUnknown()

    context = Context()
    with pytest.raises(OutcomeUnknown):
        asyncio.run(ReActLoop(analyse, decide, unknown).run(context))
    assert context.observations == 1


def test_audit_sink_failure_prevents_the_next_action_and_does_not_retry():
    def audit(transition: Transition):
        if transition.following == Phase.ACT:
            raise RuntimeError("audit_unavailable")

    context = Context()
    with pytest.raises(RuntimeError, match="audit_unavailable"):
        asyncio.run(ReActLoop(analyse, decide, act).run(context, observe=audit))
    assert context.observations == 0


def test_shared_scheduler_has_no_cross_run_state_or_observation_leakage():
    async def run():
        loop = ReActLoop(analyse, decide, act)
        one, two = Context("one"), Context("two")
        result = await asyncio.gather(loop.run(one), loop.run(two))
        assert [r.result for r in result] == [{"answer": "one"}, {"answer": "two"}]
        assert one.observations == two.observations == 1

    asyncio.run(run())


def test_control_state_is_immutable():
    with pytest.raises(FrozenInstanceError):
        LoopState().finished = True


def test_full_application_imports_with_agent_framework_imports_blocked():
    root = Path(__file__).resolve().parents[2]
    code = """
import importlib.abc
import sys
class Forbidden(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"langgraph", "langchain", "langchain_core", "langchain_protocol", "langsmith"}:
            raise AssertionError("agent_framework_imported")
sys.meta_path.insert(0, Forbidden())
from workflowops.app import create_app
from workflowops.agent.runtime import AgentService
from workflowops.agent.loop import ReActLoop
print("framework_independent")
"""
    env = os.environ.copy()
    env.update(PYTHONPATH=str(root / "src"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "framework_independent" in result.stdout
