"""Explicit real-model context/memory acceptance. Separate PG fixture; metadata progress only."""

import argparse
import asyncio
import json
import time
from uuid import uuid4

from sqlalchemy import text

from yice_agent.agent.contracts import ConversationCreate, TurnRequest
from yice_agent.agent.runtime import AgentService
from yice_agent.agent.store import AgentStore, Base, Event, Result, Snapshot
from yice_agent.business.bootstrap import provision, seed
from yice_agent.business.service import BusinessService
from yice_agent.config import OWNER, RUNTIME, Settings
from yice_agent.database import make_engine
from yice_agent.identity import PERSONAS
from yice_agent.models.api import Services

A = PERSONAS["customer-a1"]


async def execute(channel, count, phase=4):
    settings = Settings.load()
    control = make_engine(settings)
    schema = "v2_test_" + uuid4().hex
    with control.begin() as c:
        assert c.exec_driver_sql("SELECT owner FROM public.schema_owner").scalars().all() == [OWNER]
        c.execute(text('CREATE SCHEMA "' + schema + '"'))
    raw = make_engine(settings, schema)
    engine = raw.execution_options(schema_translate_map={"workflowops_agent": schema})
    resources = Services.create(raw, settings)
    domain = BusinessService(raw)
    store = AgentStore(engine)
    service = AgentService(domain, resources, store)
    evidence = {
        "channel": channel,
        "source": "real_model",
        "schema": schema,
        "status": "IN_PROGRESS",
        "started": time.time(),
        "cases": [],
        "long_30_turns": "NOT_RUN",
    }
    path = RUNTIME / ("phase" + str(phase)) / ("live-" + channel + "-" + uuid4().hex[:8] + ".json")

    def save():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        provision(raw, schema)
        seed(raw, schema)
        with engine.begin() as c:
            Base.metadata.create_all(c)
        conversation = store.create(A, ConversationCreate(channel=channel))
        evidence["conversation_id"] = conversation["id"]
        oid = domain.orders(A)[0]["order_id"]
        noise = (
            "The practice passage describes a rainy courtyard, a blue notebook, quiet music, "
            "a wooden chair, a long corridor, a small lamp, a window and a garden. "
        ) * 3
        prompts = [
            "请记住演示约束：预算上限15元，不要自动支付；我的学习代号是CTX-A17。",
            "我偏好中文简短回答。",
            "纠正：我偏好英文简短回答，学习代号改为CTX-B29。预算上限仍15元，不要自动支付。",
            "电商订单已经发货后，可以直接取消吗？请引用演示规则。",
            "查询订单" + oid + "，请用真实业务字段回答。",
        ]
        prompts += ["以下是无关练习材料，无需执行操作，无需复述，简短确认即可：" + noise] * 23
        prompts += [
            "请核对纠正后有效的学习代号、偏好、预算上限和禁止自动支付的约束，返回早期原消息的逐字引用。",
            "请基于最早约束和最新纠正回答：当前代号、预算与自动支付限制是什么？用原消息引用核对。",
        ]
        for number, prompt in enumerate(prompts[:count], 1):
            started = time.monotonic()
            run = await service.submit(
                A, conversation["id"], TurnRequest(client_request_id=str(uuid4()), text=prompt)
            )
            await service.tasks[run["id"]]
            record = store.run(A, run["id"])
            snaps = store.private(A, run["id"], Snapshot)
            events = store.private(A, run["id"], Event)
            row = {
                "number": number,
                "run_id": run["id"],
                "status": record["status"],
                "model_id": record.get("model_id"),
                "result": record.get("result"),
                "model_calls": record["model_calls"],
                "tool_calls": record["tool_calls"],
                "seconds": round(time.monotonic() - started, 2),
                "context": [
                    {
                        k: s[k]
                        for k in (
                            "before_tokens",
                            "after_tokens",
                            "input_budget",
                            "source",
                            "factor",
                            "reason",
                            "summary_version",
                        )
                    }
                    for s in snaps
                ],
                "events": events,
                "private_outputs": store.private(A, run["id"], Result),
            }
            evidence["cases"].append(row)
            save()
            print(
                json.dumps(
                    {
                        "channel": channel,
                        "turn": number,
                        "status": record["status"],
                        "reason": record.get("result", {}).get("reason"),
                        "seconds": row["seconds"],
                    }
                ),
                flush=True,
            )
            if record["status"] not in {"completed", "awaiting_confirmation"}:
                evidence["status"] = "FAILED"
                break
        else:
            evidence["status"] = "PASS"
            if count >= 5:
                if (
                    evidence["cases"][3]["result"].get("status") != "answered"
                    or evidence["cases"][4]["result"].get("status") != "business_result"
                ):
                    evidence["status"] = "FAILED_SEMANTIC_GATES"
        summary = store.summary(A, conversation["id"])
        evidence["summary"] = summary
        evidence["history_count"] = len(store.history(A, conversation["id"]))
        evidence["memory_candidates"] = store.memories(A, True)
        if count == 30:
            evidence["long_30_turns"] = (
                "PASS" if evidence["status"] == "PASS" and summary else "FAILED"
            )
        if count == 30 and evidence["long_30_turns"] == "PASS":
            required = ["CTX-B29", "15", "不要自动支付", "英文"]
            recall = json.dumps(evidence["cases"][-1]["result"], ensure_ascii=False)
            summary_text = json.dumps(summary["content"], ensure_ascii=False)
            checks = {key: key in recall and key in summary_text for key in required}
            evidence["preserved_fact_checks"] = checks
            if not all(checks.values()):
                evidence["long_30_turns"] = "FAILED"
                evidence["status"] = "FAILED_FACT_RETENTION"
        evidence["ended"] = time.time()
        save()
        print(
            json.dumps(
                {
                    "evidence": str(path),
                    "status": evidence["status"],
                    "summary_version": summary["version"] if summary else None,
                }
            ),
            flush=True,
        )
        return 0 if evidence["status"] == "PASS" else 1
    finally:
        await service.close()
        await resources.close()
        engine.dispose()
        raw.dispose()
        # This exact UUID schema was created by this invocation, never provided externally.
        assert schema.startswith("v2_test_") and len(schema) == 40
        with control.begin() as c:
            c.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        control.dispose()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--channel", required=True, choices=["qwen", "deepseek", "chatgpt"])
    p.add_argument("--turns", type=int, default=5, choices=[1, 3, 5, 30])
    p.add_argument("--approved", action="store_true")
    p.add_argument("--phase", type=int, choices=[4, 5], default=4)
    a = p.parse_args()
    if not a.approved:
        raise SystemExit("explicit_real_call_approval_required")
    raise SystemExit(asyncio.run(execute(a.channel, a.turns, a.phase)))


if __name__ == "__main__":
    main()
