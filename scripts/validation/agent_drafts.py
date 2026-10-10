"""Actual Agent drafts on all channels; disposable owned fixture, no confirmations."""

import argparse
import asyncio
import json
from uuid import uuid4

from sqlalchemy import text

from yice_agent.agent.contracts import ConversationCreate, TurnRequest
from yice_agent.agent.runtime import AgentService
from yice_agent.agent.store import AgentStore, Base, Event, Result
from yice_agent.business.bootstrap import provision, seed
from yice_agent.business.service import BusinessService
from yice_agent.config import OWNER, RUNTIME, Settings
from yice_agent.database import make_engine
from yice_agent.identity import PERSONAS
from yice_agent.models.api import Services


async def main(channels=("deepseek", "chatgpt", "qwen")):
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
    business = BusinessService(raw)
    service = AgentService(business, resources, store)
    actor = PERSONAS["customer-a1"]
    report = {"status": "IN_PROGRESS", "source": "real_agent_models", "cases": []}
    path = RUNTIME / "phase4" / ("drafts-" + uuid4().hex[:8] + ".json")
    try:
        provision(raw, schema)
        seed(raw, schema)
        with engine.begin() as c:
            Base.metadata.create_all(c)
        oid = next(
            r["order_id"]
            for r in business.orders(actor)
            if r["status"] == "awaiting_payment" and r["payment_status"] == "unpaid"
        )
        initial = business.order(actor, oid)
        for channel in channels:
            c = store.create(actor, ConversationCreate(channel=channel))
            run = await service.submit(
                actor,
                c["id"],
                TurnRequest(
                    client_request_id=str(uuid4()),
                    text="为订单" + oid + "生成支付草案；不要执行支付，等我在界面确认。",
                ),
            )
            await service.tasks[run["id"]]
            record = store.run(actor, run["id"])
            row = {
                "channel": channel,
                "run": record,
                "events": store.private(actor, run["id"], Event),
                "private_outputs": store.private(actor, run["id"], Result),
            }
            row["order_unchanged"] = business.order(actor, oid) == initial
            row["passed"] = (
                record["status"] == "awaiting_confirmation"
                and record["result"]["action"]["status"] == "pending_confirmation"
                and row["order_unchanged"]
            )
            report["cases"].append(row)
            path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "channel": channel,
                        "status": record["status"],
                        "passed": row["passed"],
                        "reason": record.get("result", {}).get("reason"),
                    }
                ),
                flush=True,
            )
        report["status"] = "PASS" if all(r["passed"] for r in report["cases"]) else "FAILED"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"evidence": str(path), "status": report["status"]}), flush=True)
        return 0 if report["status"] == "PASS" else 1
    finally:
        await service.close()
        await resources.close()
        engine.dispose()
        raw.dispose()
        assert schema.startswith("v2_test_") and len(schema) == 40
        with control.begin() as c:
            c.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        control.dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--approved", action="store_true")
    p.add_argument("--channel", choices=["all", "deepseek", "chatgpt", "qwen"], default="all")
    args = p.parse_args()
    if not args.approved:
        raise SystemExit("explicit_live_approval_required")
    raise SystemExit(asyncio.run(main() if args.channel == "all" else main((args.channel,))))
