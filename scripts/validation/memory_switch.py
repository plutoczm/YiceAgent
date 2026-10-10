"""Explicit real BGE + Qdrant memory lifecycle and same-conversation channel switches."""

import argparse
import asyncio
import json
import time
from uuid import uuid4

from sqlalchemy import text

from yice_agent.agent.contracts import (
    CandidateInput,
    ConversationConfig,
    ConversationCreate,
    MemoryConfirm,
    MemoryDelete,
    MemoryEdit,
    TurnRequest,
)
from yice_agent.agent.runtime import AgentService
from yice_agent.agent.store import AgentStore, Base, Snapshot
from yice_agent.business.bootstrap import provision, seed
from yice_agent.business.contracts import OrderAction
from yice_agent.business.service import BusinessService
from yice_agent.config import OWNER, RUNTIME, Settings
from yice_agent.database import make_engine
from yice_agent.identity import PERSONAS
from yice_agent.model_store import ModelError
from yice_agent.models.api import Services


async def execute(phase=4):
    settings = Settings.load()
    control = make_engine(settings)
    schema = "v2_test_" + uuid4().hex
    with control.begin() as c:
        assert c.exec_driver_sql("SELECT owner FROM public.schema_owner").scalars().all() == [OWNER]
        c.execute(text('CREATE SCHEMA "' + schema + '"'))
    raw = make_engine(settings, schema)
    engine = raw.execution_options(schema_translate_map={"workflowops_agent": schema})
    resources = Services.create(raw, settings)
    store = AgentStore(engine)
    domain = BusinessService(raw)
    service = AgentService(domain, resources, store)
    actor = PERSONAS["customer-a1"]
    report = {
        "status": "IN_PROGRESS",
        "source": "real_models_and_real_index",
        "schema": schema,
        "steps": [],
    }
    path = RUNTIME / ("phase" + str(phase)) / ("memory-switch-" + uuid4().hex[:8] + ".json")
    indexed = []

    def record(kind, data):
        report["steps"].append({"kind": kind, **data})
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps({"step": kind, "status": data.get("status")}, ensure_ascii=False), flush=True
        )

    try:
        provision(raw, schema)
        seed(raw, schema)
        with engine.begin() as c:
            Base.metadata.create_all(c)
        c = store.create(actor, ConversationCreate(channel="deepseek"))
        run = await service.submit(
            actor,
            c["id"],
            TurnRequest(
                client_request_id=str(uuid4()), text="我偏好中文简短回答。请将其提出为记忆候选。"
            ),
        )
        await service.tasks[run["id"]]
        result = store.run(actor, run["id"])
        assert result["status"] == "completed", "candidate_run_failed"
        can = next(
            x
            for x in store.memories(actor, True)
            if x["status"] == "pending" and "中文" in x["text"]
        )
        m = store.memory_command(
            actor,
            can["id"],
            MemoryConfirm(
                client_request_id=str(uuid4()), expected_version=can["version"], confirm=True
            ),
            "confirm",
        )
        indexed.append(m["id"])
        index = await service.memory.update(actor, m)
        assert index["status"] == "ready", "real_embedding_failed"
        record(
            "confirm_and_real_embedding",
            {
                "status": "PASS",
                "memory": m,
                "index": index,
                "confirmation_source": "explicit_acceptance_fixture",
            },
        )
        other = store.create(actor, ConversationCreate(channel="deepseek"))
        recalled = await service.memory.search(actor, "我偏好怎样回答")
        assert any(x["id"] == m["id"] and "中文" in x["text"] for x in recalled), (
            "real_recall_failed"
        )
        record(
            "cross_conversation_recall",
            {"status": "PASS", "conversation_id": other["id"], "matches": recalled},
        )
        m = store.memory_command(
            actor,
            m["id"],
            MemoryEdit(
                client_request_id=str(uuid4()),
                expected_version=m["version"],
                text="我偏好英文详细回答。",
            ),
            "edit",
        )
        index = await service.memory.update(actor, m)
        assert index["status"] == "ready"
        recalled = await service.memory.search(actor, "我偏好怎样回答")
        assert any(
            x["id"] == m["id"] and x["version"] == 2 and "英文" in x["text"] for x in recalled
        )
        assert not any("中文" in x["text"] for x in recalled)
        record(
            "edit_old_value_inactive",
            {"status": "PASS", "memory": m, "matches": recalled, "index": index},
        )
        # Keep a second active memory, so deletion filtering is tested after an actual vector query.
        seedrun, _ = store.begin(actor, other["id"], str(uuid4()), {"mode": "fixture_stable_fact"})
        msg = store.append(seedrun["id"], {"role": "user", "content": "我的学习代号是MEM-DEMO。"})
        store.finish(seedrun["id"], "completed")
        second = store.candidate(
            actor,
            other["id"],
            msg["id"],
            CandidateInput(key="learning_code", kind="stable_fact", quote=msg["content"]),
            "explicit_fixture",
        )
        second = store.memory_command(
            actor,
            second["id"],
            MemoryConfirm(client_request_id=str(uuid4()), expected_version=1, confirm=True),
            "confirm",
        )
        indexed.append(second["id"])
        assert (await service.memory.update(actor, second))["status"] == "ready"
        deleted = store.memory_command(
            actor,
            m["id"],
            MemoryDelete(client_request_id=str(uuid4()), expected_version=m["version"]),
            "delete",
        )
        original = service.memory.vectors.api

        async def delete_fault(method, path, value=None):
            if "/points/delete" in path:
                raise ModelError("injected_vector_delete_failure")
            return await original(method, path, value)

        service.memory.vectors.api = delete_fault
        try:
            fault = await service.memory.update(actor, deleted)
            assert fault["status"] == "failed"
            # Verify that the real stale point remains physically in Qdrant.
            stale = await original(
                "POST",
                "/collections/" + service.memory.collection + "/points/count",
                {
                    "exact": True,
                    "filter": {
                        "must": service.memory.filters(actor)
                        + [{"key": "memory_id", "match": {"value": m["id"]}}]
                    },
                },
            )
            assert stale["count"] == 1
            matches = await service.memory.search(actor, "我偏好英文详细回答")
            assert not any(x["id"] == m["id"] for x in matches)
            assert store.resolve_memories(actor, [(m["id"], 2)]) == []
            record(
                "delete_fault_authority_filter",
                {
                    "status": "PASS",
                    "fault_injected": True,
                    "stale_vector_count": stale["count"],
                    "index_result": fault,
                    "matches": matches,
                },
            )
        finally:
            service.memory.vectors.api = original
        assert (await service.memory.update(actor, deleted))["status"] == "ready"
        # Reuse the second preference for an Agent recall across model switches.
        second = store.memory_command(
            actor,
            second["id"],
            MemoryEdit(
                client_request_id=str(uuid4()), expected_version=1, text="我偏好英文详细回答。"
            ),
            "edit",
        )
        assert (await service.memory.update(actor, second))["status"] == "ready"
        # An explicit human fixture prepares a pending action; no model or confirmation executes it.
        oid = next(
            x["order_id"]
            for x in domain.orders(actor)
            if x["status"] == "awaiting_payment" and x["payment_status"] == "unpaid"
        )
        pending, _ = store.begin(actor, other["id"], str(uuid4()), {"mode": "fixture_draft"})
        store.append(
            pending["id"],
            {"role": "user", "content": "先为订单" + oid + "准备支付草案，等我确认。"},
        )
        action = domain.propose(
            actor, OrderAction(client_request_id=str(uuid4()), kind="pay", order_id=oid)
        )
        store.update_run(pending["id"], action_id=action["action_id"])
        store.append(
            pending["id"],
            {"role": "assistant", "content": json.dumps({"action": action}, ensure_ascii=False)},
        )
        store.finish(pending["id"], "awaiting_confirmation")
        before = domain.order(actor, oid)
        for channel, window in [("deepseek", 8192), ("chatgpt", 16384), ("qwen", 8192)]:
            store.configure(
                actor, other["id"], ConversationConfig(channel=channel, context_window=window)
            )
            run = await service.submit(
                actor,
                other["id"],
                TurnRequest(
                    client_request_id=str(uuid4()),
                    text="请从已确认长期记忆回忆我的回答偏好，只核对记忆来源，不执行业务动作。",
                ),
            )
            await service.tasks[run["id"]]
            r = store.run(actor, run["id"])
            assert r["status"] == "completed", "switch_run_failed"
            assert "英文" in r["result"].get("answer", "") and "详细" in r["result"]["answer"], (
                "switch_memory_lost"
            )
            snaps = store.private(actor, run["id"], Snapshot)
            actual = snaps[-1]
            assert actual["channel"] == channel and actual["context_window"] == window
            body = json.dumps(actual["messages"], ensure_ascii=False)
            assert action["action_id"] in body and "pending_confirmation" in body
            assert domain.order(actor, oid) == before
            record(
                "switch_" + channel,
                {
                    "status": "PASS",
                    "run_id": run["id"],
                    "model_id": r["model_id"],
                    "window": window,
                    "context": actual,
                    "result": r["result"],
                    "tool_calls": r["tool_calls"],
                },
            )
        assert service.store.memories(PERSONAS["customer-a2"]) == []
        assert service.store.memories(PERSONAS["customer-b1"]) == []
        report["status"] = "PASS"
        report["ended"] = time.time()
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"evidence": str(path), "status": "PASS"}), flush=True)
        return 0
    except Exception as error:
        report["status"] = "FAILED"
        report["error_type"] = type(error).__name__
        report["gate"] = (
            str(error) if isinstance(error, AssertionError) else "acceptance_operation_failed"
        )
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps({"evidence": str(path), "status": "FAILED", "gate": report["gate"]}),
            flush=True,
        )
        return 1
    finally:
        # Delete only records created by this fixture; never drop a shared vector collection.
        for mid in indexed:
            row = store.memory(actor, mid)
            if row and row["status"] == "active":
                row = store.memory_command(
                    actor,
                    mid,
                    MemoryDelete(client_request_id=str(uuid4()), expected_version=row["version"]),
                    "delete",
                )
            if row:
                await service.memory.update(actor, row)
        await service.close()
        await resources.close()
        engine.dispose()
        raw.dispose()
        assert schema.startswith("v2_test_") and len(schema) == 40
        with control.begin() as c:
            c.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        control.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--phase", type=int, choices=[4, 5], default=4)
    args = parser.parse_args()
    if not args.approved:
        raise SystemExit("explicit_real_acceptance_approval_required")
    raise SystemExit(asyncio.run(execute(args.phase)))


if __name__ == "__main__":
    main()
