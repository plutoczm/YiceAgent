"""Actual isolated PostgreSQL history, duplicate turns and authority filtering."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from yice_agent.agent.contracts import (
    CandidateInput,
    ConversationConfig,
    ConversationCreate,
    MemoryConfirm,
    MemoryDelete,
    MemoryEdit,
)
from yice_agent.agent.store import AgentStore, Base, Memory
from yice_agent.config import Settings
from yice_agent.database import make_engine
from yice_agent.identity import PERSONAS
from yice_agent.model_store import ModelError

pytestmark = pytest.mark.integration


@pytest.fixture
def agent_store(business):
    _, schema = business
    control = make_engine(Settings.load(), schema)
    engine = control.execution_options(schema_translate_map={"workflowops_agent": schema})
    with engine.begin() as c:
        Base.metadata.create_all(c)
    try:
        yield AgentStore(engine)
    finally:
        engine.dispose()
        control.dispose()


A = PERSONAS["customer-a1"]
B = PERSONAS["customer-b1"]
OTHER = PERSONAS["customer-a2"]


def make(store):
    return store.create(A, ConversationCreate())


def candidate(store, cid, text="我偏好中文"):
    run, _ = store.begin(A, cid, str(uuid4()), {"text": text})
    msg = store.append(run["id"], {"role": "user", "content": text})
    store.finish(run["id"], "completed")
    return store.candidate(
        A, cid, msg["id"], CandidateInput(key="language", kind="preference", quote=text), "fixture"
    )


def test_duplicate_turn_and_busy_are_serialized(agent_store):
    s = agent_store
    c = make(s)
    client = str(uuid4())

    def begin():
        return s.begin(A, c["id"], client, {"text": "hello"})

    with ThreadPoolExecutor(4) as p:
        rows = list(p.map(lambda _: begin(), range(4)))
    assert sum(fresh for _, fresh in rows) == 1
    assert len({r["id"] for r, _ in rows}) == 1
    with pytest.raises(ModelError, match="turn_request_conflict"):
        s.begin(A, c["id"], client, {"text": "different"})
    with pytest.raises(ModelError, match="conversation_busy"):
        s.begin(A, c["id"], str(uuid4()), {"text": "second"})
    with pytest.raises(ModelError, match="conversation_busy"):
        s.configure(A, c["id"], ConversationConfig(channel="deepseek"))
    s.finish(rows[0][0]["id"], "completed")
    assert s.configure(A, c["id"], ConversationConfig(channel="deepseek"))["channel"] == "deepseek"


def test_all_conversation_debug_data_are_subject_scoped(agent_store):
    s = agent_store
    c = make(s)
    r, _ = s.begin(A, c["id"], str(uuid4()), {})
    s.append(r["id"], {"role": "user", "content": "private"})
    assert s.conversations(B) == [] and s.conversations(OTHER) == []
    for actor in (B, OTHER, PERSONAS["operator-a"]):
        with pytest.raises(ModelError, match="conversation_not_found"):
            s.history(actor, c["id"])
        with pytest.raises(ModelError):
            s.run(actor, r["id"])


def test_memory_confirmation_edit_and_deleted_stale_index_filtered(agent_store):
    s = agent_store
    c = make(s)
    can = candidate(s, c["id"])
    assert s.memories(A) == []
    confirm = MemoryConfirm(client_request_id=str(uuid4()), expected_version=1, confirm=True)
    m = s.memory_command(A, can["id"], confirm, "confirm")
    assert s.memory_command(A, can["id"], confirm, "confirm") == m
    assert s.memories(A)[0]["text"] == "我偏好中文" and s.memories(B) == []
    edit = MemoryEdit(
        client_request_id=str(uuid4()), expected_version=1, text="我偏好英文", expires_in_days=7
    )
    newer = s.memory_command(A, m["id"], edit, "edit")
    assert newer["version"] == 2 and newer["versions"][0]["text"] == "我偏好中文"
    assert s.resolve_memories(A, [(m["id"], 1)]) == []
    assert s.resolve_memories(A, [(m["id"], 2)])[0]["text"] == "我偏好英文"
    deleted = s.memory_command(
        A, m["id"], MemoryDelete(client_request_id=str(uuid4()), expected_version=2), "delete"
    )
    assert deleted["status"] == "deleted" and s.resolve_memories(A, [(m["id"], 2)]) == []
    assert s.memories(A) == []


def test_memory_expiry_source_and_correction_invalidates_candidate(agent_store):
    s = agent_store
    c = make(s)
    can = candidate(s, c["id"])
    run, _ = s.begin(A, c["id"], str(uuid4()), {})
    msg = s.append(run["id"], {"role": "user", "content": "纠正：我偏好英文"})
    s.candidate(
        A,
        c["id"],
        msg["id"],
        CandidateInput(key="language", kind="preference", quote="我偏好英文"),
        "fixture",
        True,
    )
    assert next(r for r in s.memories(A, True) if r["id"] == can["id"])["status"] == "invalidated"
    with pytest.raises(ModelError, match="memory_candidate_inactive"):
        s.memory_command(
            A,
            can["id"],
            MemoryConfirm(client_request_id=str(uuid4()), expected_version=2, confirm=True),
            "confirm",
        )
    active = next(r for r in s.memories(A, True) if r["status"] == "pending")
    m = s.memory_command(
        A,
        active["id"],
        MemoryConfirm(client_request_id=str(uuid4()), expected_version=1, confirm=True),
        "confirm",
    )
    from datetime import timedelta

    from yice_agent.agent.store import now

    with Session(s.engine) as sess, sess.begin():
        sess.get(Memory, m["id"]).expires = now() - timedelta(seconds=1)
    assert s.memories(A) == [] and s.resolve_memories(A, [(m["id"], 1)]) == []


def test_restart_recovers_without_replay_and_raw_history_is_durable(agent_store):
    s = agent_store
    c = make(s)
    run, _ = s.begin(A, c["id"], str(uuid4()), {})
    s.append(run["id"], {"role": "user", "content": "原始请求"})
    fresh = AgentStore(s.engine)
    fresh.recover()
    assert fresh.run(A, run["id"])["status"] == "interrupted"
    assert fresh.history(A, c["id"])[0]["content"] == "原始请求"
    assert fresh.conversation(A, c["id"])["active_run"] is None


def test_task_state_versions_survive_reload_and_owner_filtered(agent_store):
    from yice_agent.agent.task_state import TaskProposal

    store = agent_store
    c = make(store)
    run, _ = store.begin(A, c["id"], str(uuid4()), {})
    store.append(run["id"], {"role": "user", "content": "预算200元，不要自动支付。"})
    first = store.sync_task_state(A, run["id"])
    store.finish(run["id"], "completed")
    run, _ = store.begin(A, c["id"], str(uuid4()), {})
    message = store.append(run["id"], {"role": "user", "content": "纠正：预算改为100元。"})
    second = store.sync_task_state(A, run["id"])
    same = store.sync_task_state(
        A, run["id"], [TaskProposal(key="budget_cny", quote="100元")], message["id"]
    )
    assert same.version == second.version
    reloaded = AgentStore(store.engine)
    assert reloaded.task_state(A, c["id"]) == second
    assert [v["version"] for v in reloaded.task_states(A, c["id"])] == [
        first.version,
        second.version,
    ]
    assert any(f.value == "200" and f.status == "superseded" for f in second.facts)
    for actor in (B, OTHER):
        with pytest.raises(ModelError):
            reloaded.task_states(actor, c["id"])
        with pytest.raises(ModelError):
            reloaded.sync_task_state(actor, run["id"])


def test_task_state_sync_concurrency_and_invalid_patch_zero_new_versions(agent_store):
    from yice_agent.agent.task_state import TaskProposal

    store = agent_store
    c = make(store)
    run, _ = store.begin(A, c["id"], str(uuid4()), {})
    message = store.append(run["id"], {"role": "user", "content": "预算100元。"})
    with ThreadPoolExecutor(4) as pool:
        states = list(pool.map(lambda _: store.sync_task_state(A, run["id"]), range(4)))
    assert all(state.version == 1 for state in states)
    assert len(store.task_states(A, c["id"])) == 1
    with pytest.raises(ModelError, match="task_state_source_invalid"):
        store.sync_task_state(
            A, run["id"], [TaskProposal(key="budget", quote="500元")], message["id"]
        )
    assert len(store.task_states(A, c["id"])) == 1
