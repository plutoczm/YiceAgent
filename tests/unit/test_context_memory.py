"""Offline invariants; fake generations validate controls, not real model quality."""

import pytest

from yice_agent.agent.context import canonical, groups, protected, validate_summary
from yice_agent.agent.contracts import ConversationCreate, MemoryEdit, SummaryOutput, TurnRequest
from yice_agent.agent.store import safe_memory
from yice_agent.agent.tools import LoopGuard
from yice_agent.contracts import GenerationRequest, Message, ToolCall
from yice_agent.model_store import ModelError
from yice_agent.models.http import validate_request
from yice_agent.tokens import estimate


def test_estimator_includes_schema_tools_and_is_explicitly_estimated():
    r = GenerationRequest(messages=[Message(role="user", content="中文问题" * 40)])
    measure = estimate(r)
    assert measure["input_tokens"] > 0 and not measure["exact_for_provider"]
    with_schema = r.model_copy(update={"output_schema": {"description": "contract" * 100}})
    assert estimate(with_schema)["input_tokens"] > measure["input_tokens"]
    validate_request(r)


def test_huge_context_and_credentials_fail_before_model():
    with pytest.raises(ModelError, match="context_capacity_exceeded"):
        validate_request(
            GenerationRequest(messages=[Message(role="user", content="abcdefgh中文" * 10000)])
        )
    with pytest.raises(ModelError, match="credential_in_model_context"):
        validate_request(
            GenerationRequest(messages=[Message(role="user", content="sk-" + "a" * 32)])
        )


def test_closed_inputs_reject_tenant_and_permissions():
    for cls, data in [
        (ConversationCreate, {"tenant": "tenant-b"}),
        (TurnRequest, {"text": "hello", "client_request_id": "0" * 36, "role": "operator"}),
        (MemoryEdit, {"expected_version": 1, "text": "x", "tenant": "b"}),
    ]:
        with pytest.raises(ValueError):
            cls.model_validate(data)


def test_tool_groups_never_split_or_reuse_provider_ids():
    call = ToolCall(id="call-1", name="read_order", arguments={"order_id": "ORD-A"})
    a = {
        "id": "a",
        "run_id": "12345678-rest",
        "position": 1,
        **Message(role="assistant", tool_calls=[call]).model_dump(mode="json"),
    }
    t = {
        "id": "b",
        "run_id": a["run_id"],
        "position": 2,
        **Message(role="tool", tool_call_id=call.id, content="{}").model_dump(mode="json"),
    }
    assert groups([a]) == []
    assert len(groups([a, t])) == 1
    assert canonical(a).tool_calls[0].id == canonical(t).tool_call_id
    second = {**a, "run_id": "87654321-rest"}
    assert canonical(a).tool_calls[0].id != canonical(second).tool_calls[0].id


def summary_data():
    row = {"id": "source", "role": "user", "content": "预算上限15元，不能自动支付", "position": 1}
    payload = {
        "goals": [],
        "constraints": [{"message_id": "source", "quote": row["content"]}],
        "corrections": [],
        "confirmed": [],
        "unresolved": [],
    }
    return row, payload


def test_summary_must_keep_exact_constraints_and_original_refs():
    row, payload = summary_data()
    valid = SummaryOutput.model_validate(payload)
    assert validate_summary(valid, [row], protected([row]))["constraints"]
    payload["constraints"][0]["quote"] = "预算上限"
    with pytest.raises(ModelError, match="summary_protected_fact_missing"):
        validate_summary(SummaryOutput.model_validate(payload), [row], protected([row]))
    payload["constraints"][0]["quote"] = "预算上限150元"
    with pytest.raises(ModelError, match="summary_source_invalid"):
        validate_summary(SummaryOutput.model_validate(payload), [row], [])


@pytest.mark.parametrize(
    "value", ["sk-" + "a" * 32, "我的密码是secret", "订单已支付", "支付成功", "审批结果通过"]
)
def test_credentials_and_durable_business_facts_not_memories(value):
    with pytest.raises(ModelError):
        safe_memory(value)


def test_identical_read_new_version_allowed_but_same_version_stops():
    guard = LoopGuard()
    guard.before("read_order", {"order_id": "ORD-A"}, 1)
    guard.before("read_order", {"order_id": "ORD-A"}, 2)
    with pytest.raises(ModelError, match="repeated_tool_call"):
        guard.before("read_order", {"order_id": "ORD-A"}, 2)


def test_periodic_cycle_and_no_new_information_stop():
    guard = LoopGuard()
    for name in ("one", "two", "one"):
        guard.before(name, {}, 1)
    with pytest.raises(ModelError, match="tool_call_cycle"):
        guard.before("two", {}, 1)
    guard = LoopGuard()
    guard.after({"version": 1})
    for _ in range(2):
        guard.after({"version": 1})
    with pytest.raises(ModelError, match="tool_no_new_information"):
        guard.after({"version": 1})
