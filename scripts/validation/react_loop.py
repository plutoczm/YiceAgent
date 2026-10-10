"""Explicitly approved real-provider proof for the self-built ReAct scheduler.

Uses one owned disposable business/Agent schema, original shared RAG and billing
resources, and real responses. Never confirms model-proposed drafts.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text

from yice_agent.agent.contracts import ConversationCreate, TurnRequest
from yice_agent.agent.loop import ENGINE_ID
from yice_agent.agent.runtime import AgentService
from yice_agent.agent.store import AgentStore, Base, Event, Result
from yice_agent.business.bootstrap import provision, seed
from yice_agent.business.contracts import DRAFT, Confirmation
from yice_agent.business.service import BusinessService
from yice_agent.config import OWNER, RUNTIME, Settings
from yice_agent.database import make_engine
from yice_agent.identity import PERSONAS
from yice_agent.model_store import ModelStore
from yice_agent.models.api import Services

ACTOR = PERSONAS["customer-a1"]


async def main(channels, selected_cases=None):
    settings = Settings.load()
    control = make_engine(settings)
    schema = "v2_test_" + uuid4().hex
    path = RUNTIME / "react-loop" / ("live-" + uuid4().hex[:8] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with control.begin() as conn:
        assert conn.exec_driver_sql("SELECT owner FROM public.schema_owner").scalars().all() == [
            OWNER
        ]
        conn.execute(text('CREATE SCHEMA "' + schema + '"'))
    raw = make_engine(settings, schema)
    engine = raw.execution_options(schema_translate_map={"workflowops_agent": schema})
    resources = Services.create(raw, settings)
    store = AgentStore(engine)
    business = BusinessService(raw)
    service = AgentService(business, resources, store)
    report = {
        "status": "IN_PROGRESS",
        "at": datetime.now(UTC).isoformat(),
        "engine": ENGINE_ID,
        "schema": schema,
        "cases": [],
        "cleaned": False,
        "before_budget": ModelStore(raw).status(ACTOR.tenant_id + ":" + ACTOR.subject),
        "source": "real_providers_real_models_and_owned_postgres",
        "scope": "focused_scheduler_regression_not_full_30_turn_repeat",
    }

    def save():
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        provision(raw, schema)
        seed(raw, schema)
        with engine.begin() as conn:
            Base.metadata.create_all(conn)
        initial = business.order(ACTOR, "ECO-A-01")
        # This deterministic seed is explicit test setup, never a model instruction.
        draft = business.propose(
            ACTOR,
            DRAFT.validate_python(
                {
                    "client_request_id": str(uuid4()),
                    "kind": "create_ticket",
                    "order_id": "ECO-A-01",
                    "message": "Runtime validation fixture",
                }
            ),
        )
        business.confirm(
            ACTOR,
            draft["action_id"],
            Confirmation(
                client_request_id=str(uuid4()), confirm=True, expected_version=draft["version"]
            ),
        )
        cases = [
            ("lookup", "查询订单ECO-A-01的当前状态，只返回本轮查询的业务字段。"),
            ("policy", "电商订单已发货后能取消吗？请引用当前业务规则。"),
            ("draft", "为订单ECO-A-01生成支付草案。不要执行支付，等待界面确认。"),
            (
                "feedback",
                "查询我名下的工单：先调用list_tickets获取列表，再调用read_result读取刚返回的result_id原始结果，最后返回工单状态。只读查询，不创建或修改工单。",
            ),
        ]
        if selected_cases is not None:
            cases = [(label, prompt) for label, prompt in cases if label in selected_cases]
        report["requested_cases"] = [label for label, _ in cases]
        for channel in channels:
            for label, prompt in cases:
                conversation = store.create(
                    ACTOR, ConversationCreate(channel=channel, title="ReAct " + label)
                )
                record = await service.submit(
                    ACTOR,
                    conversation["id"],
                    TurnRequest(client_request_id=str(uuid4()), text=prompt),
                )
                await service.tasks[record["id"]]
                run = store.run(ACTOR, record["id"])
                events = store.private(ACTOR, run["id"], Event)
                trace = [e for e in events if e["kind"] == "loop_transition"]
                result = run.get("result", {})
                passed = bool(trace and trace[-1]["to"] == "finish")
                if label == "lookup":
                    passed &= run["status"] == "completed" and any(
                        row.get("order_id") == "ECO-A-01" for row in result.get("business", [])
                    )
                elif label == "policy":
                    passed &= run["status"] == "completed" and bool(result.get("citations"))
                elif label == "draft":
                    passed &= (
                        run["status"] == "awaiting_confirmation"
                        and result.get("action", {}).get("kind") == "pay"
                        and result.get("action", {}).get("status") == "pending_confirmation"
                    )
                else:
                    used = [e.get("tool") for e in events if e["kind"] == "tool_finished"]
                    passed &= (
                        run["status"] == "completed"
                        and "list_tickets" in used
                        and "read_result" in used
                    )
                    passed &= any(e["from"] == "act" and e["to"] == "decide" for e in trace)
                    passed &= not result.get("action")
                unchanged = business.order(ACTOR, "ECO-A-01") == initial
                passed &= unchanged and all(e["engine"] == ENGINE_ID for e in trace)
                row = {
                    "channel": channel,
                    "case": label,
                    "passed": bool(passed),
                    "run": run,
                    "events": events,
                    "order_unchanged": unchanged,
                    "private_outputs": store.private(ACTOR, run["id"], Result),
                }
                report["cases"].append(row)
                save()
                print(
                    json.dumps(
                        {
                            "channel": channel,
                            "case": label,
                            "status": run["status"],
                            "passed": bool(passed),
                            "reason": result.get("reason"),
                            "dispatches": len(trace),
                            "model_calls": run["model_calls"],
                            "tool_calls": run["tool_calls"],
                        }
                    ),
                    flush=True,
                )
        report["status"] = "PASS" if all(c["passed"] for c in report["cases"]) else "FAILED"
    except Exception as error:
        report.update(status="FAILED", error_type=type(error).__name__)
    finally:
        await service.close()
        await resources.close()
        report["after_budget"] = ModelStore(raw).status(ACTOR.tenant_id + ":" + ACTOR.subject)
        engine.dispose()
        raw.dispose()
        assert schema.startswith("v2_test_") and len(schema) == 40
        with control.begin() as conn:
            conn.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        report["cleaned"] = True
        control.dispose()
        save()
        print(
            json.dumps({"status": report["status"], "evidence": str(path), "cleaned": True}),
            flush=True,
        )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--channel", choices=["all", "deepseek", "chatgpt", "qwen"], default="all")
    parser.add_argument(
        "--case", choices=["lookup", "policy", "draft", "feedback"], action="append"
    )
    args = parser.parse_args()
    if not args.approved:
        raise SystemExit("explicit_live_approval_required")
    targets = ("deepseek", "chatgpt", "qwen") if args.channel == "all" else (args.channel,)
    raise SystemExit(asyncio.run(main(targets, args.case)))
