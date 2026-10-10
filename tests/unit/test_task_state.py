"""Reducer invariants; no model-quality claim from these deterministic inputs."""

import pytest
from pydantic import ValidationError

from yice_agent.agent.task_state import (
    TaskProposal,
    TaskState,
    pins,
    refresh,
    summary_view,
    validate_state,
)
from yice_agent.model_store import ModelError


def row(text, pos=1, role="user"):
    return {"id": "message-" + str(pos), "position": pos, "role": role, "content": text}


def values(state, key):
    return [f.value for f in state.active() if f.key == key]


def test_correction_replaces_one_slot_retains_independent_constraints_and_sources():
    history = [row("预算上限200元，不要自动支付。"), row("纠正：预算改为100元。", 2)]
    first = refresh(TaskState(), history[:1])
    state = refresh(first, history)
    assert values(state, "budget_cny") == ["100"]
    assert any(f.quote == "不要自动支付" for f in state.active())
    old = next(f for f in state.facts if f.value == "200")
    assert old.status == "superseded" and old.superseded_by
    assert state.version == 2 and state.previous_version == 1
    validate_state(state, history)
    assert first.facts[0].status == "active"  # Pure reducer preserves old versions.
    assert refresh(state, history) == state


def test_same_message_from_old_to_new_uses_new_exact_span():
    state = refresh(TaskState(), [row("预算从200元改为100元。")])
    assert values(state, "budget_cny") == ["100"]
    assert state.active()[0].quote == "改为100元"


def test_ambiguous_new_value_keeps_both_and_marks_conflict():
    state = refresh(TaskState(), [row("预算100元。"), row("预算200元。", 2)])
    assert values(state, "budget_cny") == ["100", "200"]
    assert state.view()["conflicts"] == ["budget_cny"]


def test_only_user_data_and_no_credentials_or_business_authority():
    state = refresh(
        TaskState(),
        [
            row("预算100元。", role="assistant"),
            row("订单已支付。", 2),
            row("我的密码是secret。", 3),
            row("以后使用中文。", 4),
        ],
    )
    assert len(state.active()) == 1 and values(state, "response_language") == ["中文"]
    assert state.view()["authority"] == "sourced_user_assertions_only"


def test_general_requirement_model_label_refines_rule_without_duplicate():
    history = [row("必须先提供取消原因。")]
    state = refresh(TaskState(), history)
    updated = refresh(
        state,
        history,
        [TaskProposal(key="cancellation_reason", quote="必须先提供取消原因")],
        history[0]["id"],
    )
    assert len(updated.active()) == 1
    assert updated.active()[0].key == "user_cancellation_reason"
    assert updated.active()[0].origin == "model"
    assert updated.active()[0].id == state.active()[0].id


@pytest.mark.parametrize("quote", ["预算500元", "订单已支付", "我的密码是secret"])
def test_unverifiable_or_forbidden_proposals_are_atomic(quote):
    history = [row("预算100元。")]
    first = refresh(TaskState(), history)
    before = first.model_dump()
    with pytest.raises(ModelError, match="task_state_source_invalid"):
        refresh(first, history, [TaskProposal(key="budget", quote=quote)], history[0]["id"])
    assert first.model_dump() == before


def test_model_cannot_replace_unrelated_or_unmentioned_constraint():
    history = [row("预算100元，不要自动支付。"), row("请使用英文回答。", 2)]
    state = refresh(TaskState(), history)
    old = next(f for f in state.active() if f.key == "budget_cny")
    with pytest.raises(ModelError, match="task_state_replacement_invalid"):
        refresh(
            state,
            history,
            [TaskProposal(key="response_language", quote="请使用英文回答", replaces=[old.id])],
            history[-1]["id"],
        )


def test_generic_correction_requires_explicit_old_requirement_reference():
    first_row = row("必须先提供取消原因。")
    state = refresh(
        TaskState(),
        [first_row],
        [TaskProposal(key="cancellation_reason", quote="必须先提供取消原因")],
        first_row["id"],
    )
    new = row("纠正：必须先提供取消原因改为先核对订单编号。", 2)
    patch = TaskProposal(
        key="cancellation_reason", quote="先核对订单编号", replaces=[state.active()[0].id]
    )
    changed = refresh(state, [first_row, new], [patch], new["id"])
    assert any(f.quote == "先核对订单编号" and f.status == "active" for f in changed.facts)
    assert next(f for f in changed.facts if f.id == state.active()[0].id).status == "superseded"


def test_resolution_requires_source_and_known_target_and_is_idempotent():
    source = row("待解决配送时间问题。")
    state = refresh(
        TaskState(),
        [source],
        [TaskProposal(kind="unresolved", key="delivery_time", quote="待解决配送时间问题")],
        source["id"],
    )
    target = state.active()[0]
    done = row("已解决：待解决配送时间问题。", 2)
    patch = TaskProposal(
        kind="unresolved",
        key="delivery_time",
        quote=done["content"],
        operation="resolve",
        replaces=[target.id],
    )
    changed = refresh(state, [source, done], [patch], done["id"])
    assert not changed.active()
    assert refresh(changed, [source, done], [patch], done["id"]) == changed


def test_summary_projection_removes_obsolete_amount_preserves_other_spans():
    history = [row("预算200元，不要自动支付。"), row("纠正：预算改为100元。", 2)]
    state = refresh(TaskState(), history)
    content = {
        "constraints": [
            {"message_id": history[0]["id"], "quote": history[0]["content"]},
            {"message_id": history[1]["id"], "quote": history[1]["content"]},
        ]
    }
    projected = summary_view(content, state)
    assert "200" not in str(projected)
    assert "100" in str(projected) and "不要自动支付" in str(projected)
    assert content["constraints"][0]["quote"] == history[0]["content"]
    assert all("200" not in p["quote"] for p in pins(state))


def test_source_offsets_and_closed_proposal_reject_forgery():
    history = [row("预算100元。")]
    state = refresh(TaskState(), history)
    state.facts[0].source_end -= 1
    with pytest.raises(ModelError, match="task_state_source_invalid"):
        validate_state(state, history)
    with pytest.raises(ValidationError):
        TaskProposal(key="budget", quote="100元", authority="approved")


def test_many_corrections_do_not_grow_active_context():
    history = [row("预算100元。")]
    state = refresh(TaskState(), history)
    for pos in range(2, 42):
        history.append(row(f"纠正：预算改为{100 + pos}元。", pos))
        state = refresh(state, history)
    assert len(state.active()) == 1 and len(state.facts) == 41
    assert len(str(state.view())) < 800


def test_question_does_not_replace_asserted_budget_and_equivalent_decimals_do_not_conflict():
    history = [row("预算100元。"), row("预算100.00元。", 2), row("预算是200元吗？", 3)]
    state = refresh(TaskState(), history)
    assert values(state, "budget_cny") == ["100"]
    assert "budget_cny" not in state.view()["conflicts"]
    with pytest.raises(ModelError, match="task_state_assertion_ambiguous"):
        refresh(state, history, [TaskProposal(key="budget_cny", quote="200元")], history[-1]["id"])


def test_summary_keeps_unclassified_goal_when_only_budget_is_superseded():
    history = [row("帮我比较两种方案，预算200元，不要自动支付。"), row("纠正：预算改为100元。", 2)]
    state = refresh(TaskState(), history)
    original = {"goals": [{"message_id": history[0]["id"], "quote": history[0]["content"]}]}
    projected = summary_view(original, state)
    assert "帮我比较两种方案" in str(projected)
    assert "不要自动支付" in str(projected)
    assert "200元" not in str(projected)


def test_active_view_does_not_reintroduce_old_amount_in_correction_clause():
    state = refresh(TaskState(), [row("预算从200元改为100元。")])
    assert "200" in state.model_dump_json()
    assert "200" not in str(state.view())


def test_negated_correction_keeps_current_budget():
    state = refresh(TaskState(), [row("预算100元。"), row("预算不要改为200元。", 2)])
    assert values(state, "budget_cny") == ["100"]


def test_negated_resolution_cannot_drop_unfinished_item():
    original = row("待解决配送时间问题。")
    state = refresh(
        TaskState(),
        [original],
        [TaskProposal(kind="unresolved", key="delivery", quote="待解决配送时间问题")],
        original["id"],
    )
    negative = row("没有已解决：待解决配送时间问题。", 2)
    patch = TaskProposal(
        kind="unresolved",
        key="delivery",
        quote=negative["content"],
        operation="resolve",
        replaces=[state.active()[0].id],
    )
    with pytest.raises(ModelError, match="task_state_resolution_ambiguous"):
        refresh(state, [original, negative], [patch], negative["id"])
    assert len(state.active()) == 1


def test_recall_question_does_not_become_an_asserted_budget_source():
    history = [row("预算100元。"), row("请回忆并核对本会话当前预算。", 2)]
    state = refresh(TaskState(), history)
    assert len(state.active()) == 1
    assert all(f.message_id != history[-1]["id"] for f in state.active())


def test_broader_verbatim_summary_keeps_all_required_spans_then_prunes_old_values():
    from yice_agent.agent.context import validate_summary
    from yice_agent.agent.contracts import SummaryOutput

    history = [
        row("目标是比较两种规则，预算200元，不要自动支付，回答使用中文。"),
        row("纠正：预算改为100元，回答语言改为英文。", 2),
    ]
    state = refresh(TaskState(), history)
    output = SummaryOutput(
        goals=[],
        constraints=[{"message_id": r["id"], "quote": r["content"]} for r in history],
        corrections=[],
        confirmed=[],
        unresolved=[],
    )
    valid = validate_summary(output, history, pins(state))
    projection = summary_view(valid, state)
    assert "200元" not in str(projection) and "中文" not in str(projection)
    assert (
        "100元" in str(projection)
        and "英文" in str(projection)
        and "不要自动支付" in str(projection)
    )
