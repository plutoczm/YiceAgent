"""Explicitly approved focused proof of sourced task state and correction/compaction."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
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
from yice_agent.model_store import ModelStore
from yice_agent.models.api import Services
from yice_agent.models.gateway import Gateway

ACTOR = PERSONAS["customer-a1"]
SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"


async def main(channels, verified_on=None):
    settings = Settings.load()
    if "deepseek" in channels and verified_on:
        # The caller must verify the official peak rates on this date first.
        if (
            Decimal(settings.input_price_cny_per_million or "0") != Decimal("2")
            or Decimal(settings.output_price_cny_per_million or "0") != Decimal("8")
            or settings.pricing_source != SOURCE
        ):
            raise RuntimeError("verified_rates_do_not_match")
        settings = settings.model_copy(update={"pricing_checked_on": verified_on})
    control = make_engine(settings)
    schema = "v2_test_" + uuid4().hex
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
    service = AgentService(
        business, resources, store, lambda: Gateway(settings, ModelStore(raw), resources.inference)
    )
    path = RUNTIME / "task-state" / ("live-" + uuid4().hex[:8] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "IN_PROGRESS",
        "at": datetime.now(UTC).isoformat(),
        "scope": "focused_task_state_not_full_30_turn_or_strategy_comparison",
        "schema": schema,
        "channels": [],
        "cleaned": False,
        "pricing_verified_on_process_only": verified_on,
        "pricing_source": SOURCE if verified_on else None,
        "before_budget": ModelStore(raw).status(ACTOR.tenant_id + ":" + ACTOR.subject),
    }

    def save():
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    async def turn(cid, prompt, manual=False):
        record = await service.submit(
            ACTOR, cid, TurnRequest(client_request_id=str(uuid4()), text=prompt), manual=manual
        )
        task = service.tasks.get(record["id"])
        if task:
            await task
        run = store.run(ACTOR, record["id"])
        print(
            json.dumps(
                {
                    "channel": run["channel"],
                    "run_status": run["status"],
                    "model_calls": run["model_calls"],
                    "reason": run.get("result", {}).get("reason"),
                }
            ),
            flush=True,
        )
        return {
            "run": run,
            "events": store.private(ACTOR, run["id"], Event),
            "snapshots": store.private(ACTOR, run["id"], Snapshot),
            "private_results": store.private(ACTOR, run["id"], Result),
        }

    try:
        provision(raw, schema)
        seed(raw, schema)
        with engine.begin() as conn:
            Base.metadata.create_all(conn)
        before = business.order(ACTOR, "ECO-A-01")
        for channel in channels:
            c = store.create(ACTOR, ConversationCreate(channel=channel, title="任务状态验证"))
            initial = await turn(
                c["id"],
                "请记住本会话目标：比较电商与外卖的取消规则；预算上限200元，不要自动支付，回答使用中文。",
            )
            correction = await turn(c["id"], "纠正：预算改为100元，回答语言改为英文。")
            # Explicit synthetic history exercises older-history compression without claiming model turns.
            for i in range(6):
                run, _ = store.begin(ACTOR, c["id"], str(uuid4()), {"fixture": i})
                store.append(run["id"], {"role": "user", "content": "闲聊天气音乐风景。" * 8})
                store.append(run["id"], {"role": "assistant", "content": "固定测试历史。"})
                store.update_run(run["id"], intent="clarify")
                store.finish(run["id"], "completed")
            compressed = await turn(c["id"], "手动整理上下文", manual=True)
            recall = await turn(
                c["id"], "请回忆并核对本会话当前预算、回答语言和自动支付限制，只引用当前有效信息。"
            )
            state = store.task_state(ACTOR, c["id"])
            answer = json.dumps(recall["run"].get("result", {}), ensure_ascii=False)
            active = state.active()
            latest = compressed["snapshots"][-1] if compressed["snapshots"] else {}
            inputs = json.dumps(latest.get("messages", []), ensure_ascii=False)
            passed = (
                initial["run"]["status"] == "completed"
                and correction["run"]["status"] == "completed"
                and compressed["run"]["status"] == "completed"
                and recall["run"]["status"] == "completed"
                and compressed["run"].get("result", {}).get("status") == "compressed"
                and [f.value for f in active if f.key == "budget_cny"] == ["100"]
                and [f.value for f in active if f.key == "response_language"] == ["英文"]
                and "不要自动支付" in answer
                and "100" in answer
                and "英文" in answer
                and "200元" not in answer
                and "中文" not in answer
                and "200元" not in inputs
                and "中文" not in inputs
                and any(f.status == "superseded" for f in state.facts)
                and any(f.origin == "model" for f in state.facts)
                and business.order(ACTOR, "ECO-A-01") == before
            )
            report["channels"].append(
                {
                    "channel": channel,
                    "passed": bool(passed),
                    "model_ids": sorted(
                        {
                            r["run"].get("model_id", "")
                            for r in (initial, correction, compressed, recall)
                        }
                    ),
                    "state": state.model_dump(),
                    "runs": [initial, correction, compressed, recall],
                    "history": store.history(ACTOR, c["id"]),
                    "summary": store.summary(ACTOR, c["id"]),
                    "synthetic_history_turns": 6,
                    "actual_agent_turns": 3,
                    "actual_manual_compression_runs": 1,
                    "business_unchanged": business.order(ACTOR, "ECO-A-01") == before,
                }
            )
            save()
            print(json.dumps({"channel": channel, "passed": bool(passed)}), flush=True)
        report["status"] = "PASS" if all(r["passed"] for r in report["channels"]) else "FAILED"
    except Exception as error:
        report.update(status="FAILED", error_type=type(error).__name__)
    finally:
        await service.close()
        await resources.close()
        report["after_budget"] = ModelStore(raw).status(ACTOR.tenant_id + ":" + ACTOR.subject)
        engine.dispose()
        raw.dispose()
        assert len(schema) == 40 and schema.startswith("v2_test_")
        with control.begin() as conn:
            conn.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        control.dispose()
        report["cleaned"] = True
        save()
        print(
            json.dumps({"status": report["status"], "evidence": str(path), "cleaned": True}),
            flush=True,
        )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--channel", choices=["all", "qwen", "deepseek", "chatgpt"], default="all")
    parser.add_argument("--pricing-verified-on")
    args = parser.parse_args()
    if not args.approved:
        raise SystemExit("explicit_live_approval_required")
    targets = ("deepseek", "chatgpt", "qwen") if args.channel == "all" else (args.channel,)
    raise SystemExit(asyncio.run(main(targets, args.pricing_verified_on)))
