"""Real three-channel dual-business acceptance in an owned disposable PG schema.

Explicit human test fixtures confirm each action and simulate operator events.
No provider output is treated as confirmation. Failures remain in the report.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text

from workflowops.agent.contracts import ActionConfirmation, ConversationCreate, TurnRequest
from workflowops.agent.runtime import AgentService
from workflowops.agent.store import AgentStore, Base, Event, Result
from workflowops.business.bootstrap import provision, seed
from workflowops.business.contracts import DRAFT, Approval, Confirmation, SimulationEvent
from workflowops.business.service import BusinessError, BusinessService
from workflowops.config import OWNER, RUNTIME, Settings
from workflowops.database import make_engine
from workflowops.identity import PERSONAS
from workflowops.model_store import ModelStore
from workflowops.models.api import Services

A = PERSONAS["customer-a1"]
OP = PERSONAS["operator-a"]


async def main(channels, scenarios, families=("ecommerce", "food")):
    settings = Settings.load()
    control = make_engine(settings)
    schema = "v2_test_" + uuid4().hex
    path = RUNTIME / "phase5" / ("business-" + uuid4().hex[:8] + ".json")
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
        "source": "real_agent_models_and_postgres",
        "schema": schema,
        "cases": [],
        "scenarios": [],
        "before_budget": ModelStore(raw).status(A.tenant_id + ":" + A.subject),
        "cleaned": False,
    }

    def save():
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    async def turn(cid, channel, label, prompt, expected_kind=None):
        run = await service.submit(A, cid, TurnRequest(client_request_id=str(uuid4()), text=prompt))
        await service.tasks[run["id"]]
        result = store.run(A, run["id"])
        action = result.get("result", {}).get("action")
        passed = (
            (
                result["status"] == "awaiting_confirmation"
                and action
                and action["kind"] == expected_kind
            )
            if expected_kind
            else result["status"] == "completed"
        )
        report["cases"].append(
            {
                "label": label,
                "channel": channel,
                "input": prompt,
                "passed": bool(passed),
                "run": result,
                "events": store.private(A, run["id"], Event),
                "private_outputs": store.private(A, run["id"], Result),
            }
        )
        save()
        print(
            json.dumps(
                {
                    "channel": channel,
                    "case": label,
                    "status": result["status"],
                    "passed": bool(passed),
                    "reason": result.get("result", {}).get("reason"),
                }
            ),
            flush=True,
        )
        if not passed:
            raise RuntimeError("scenario_model_step_failed:" + label)
        return action or result["result"]

    async def confirm(cid, row):
        assert row["status"] == "pending_confirmation"
        request = ActionConfirmation(
            client_request_id=str(uuid4()),
            action_id=row["action_id"],
            expected_version=row["version"],
            confirm=True,
        )
        result = await service.confirm_action(A, cid, request)
        assert result["status"] == "completed", result.get("result")
        assert await service.confirm_action(A, cid, request) == result
        return result["result"]["action"]["result"]

    def sim(oid, event):
        row = business.order(OP, oid)
        body = SimulationEvent(
            client_request_id=str(uuid4()),
            order_id=oid,
            expected_version=row["version"],
            event=event,
        )
        result = business.simulate(OP, body)
        assert business.simulate(OP, body) == result
        return result

    def operator_action(**values):
        row = business.propose(
            OP, DRAFT.validate_python({"client_request_id": str(uuid4()), **values})
        )
        return business.confirm(
            OP,
            row["action_id"],
            Confirmation(
                client_request_id=str(uuid4()), confirm=True, expected_version=row["version"]
            ),
        )

    try:
        provision(raw, schema)
        seed(raw, schema)
        with engine.begin() as conn:
            Base.metadata.create_all(conn)
        for channel in channels:
            for family, prefix in [("ecommerce", "ECO"), ("food", "FOOD")]:
                if family not in families:
                    continue
                for scenario in scenarios:
                    label = channel + "/" + family + "/" + scenario
                    start = len(report["cases"])
                    record = {"label": label, "status": "IN_PROGRESS"}
                    report["scenarios"].append(record)
                    try:
                        cid = store.create(
                            A,
                            ConversationCreate(
                                channel=channel, title="P5 " + family + " " + scenario
                            ),
                        )["id"]
                        low = scenario == "delivery"
                        product = (
                            prefix
                            + "-A-"
                            + (
                                ("CABLE" if family == "ecommerce" else "TEA")
                                if low
                                else ("PHONE" if family == "ecommerce" else "FAMILY")
                            )
                        )
                        before = len(business.orders(A))
                        draft = await turn(
                            cid,
                            channel,
                            label + "/place",
                            "请下单："
                            + family
                            + "类型，商品ID为"
                            + product
                            + "，数量1，备注第五阶段演示。只生成草案，等待界面确认。",
                            "place_order",
                        )
                        assert len(business.orders(A)) == before
                        payload = draft["payload"]
                        assert payload["items"] == [{"product_id": product, "quantity": 1}]
                        oid = (await confirm(cid, draft))["order_id"]
                        assert len(business.orders(A)) == before + 1
                        draft = await turn(
                            cid,
                            channel,
                            label + "/pay",
                            "为订单" + oid + "生成支付草案，等待界面确认。",
                            "pay",
                        )
                        assert business.order(A, oid)["payment_status"] == "unpaid"
                        await confirm(cid, draft)
                        sim(oid, "payment_succeeded")
                        lookup = await turn(
                            cid, channel, label + "/lookup", "查询订单" + oid + "的实际状态"
                        )
                        assert lookup["business"][0]["order_id"] == oid
                        if low:
                            for _ in range(3 if family == "ecommerce" else 4):
                                sim(oid, "advance")
                            original = business.order(A, oid)
                            # Invalid business, ownership and replay requests cannot write.
                            for actor, kind, target in [
                                (A, "cancel_order", oid),
                                (PERSONAS["customer-a2"], "refund", oid),
                                (PERSONAS["customer-b1"], "pay", oid),
                            ]:
                                try:
                                    business.propose(
                                        actor,
                                        DRAFT.validate_python(
                                            {
                                                "client_request_id": str(uuid4()),
                                                "kind": kind,
                                                "order_id": target,
                                            }
                                        ),
                                    )
                                except BusinessError:
                                    pass
                                else:
                                    raise RuntimeError("illegal_draft_accepted")
                            assert business.order(A, oid) == original
                            draft = await turn(
                                cid,
                                channel,
                                label + "/refund",
                                "为订单" + oid + "生成整单退款草案，等待界面确认。",
                                "refund",
                            )
                            await confirm(cid, draft)
                            sim(oid, "refund_succeeded")
                            result = business.order(A, oid)
                            assert (
                                result["status"] == "delivered"
                                and result["refunded_amount"] == result["paid_amount"]
                            )
                            ticket = await turn(
                                cid,
                                channel,
                                label + "/ticket",
                                "为订单"
                                + oid
                                + "创建异常工单草案，问题描述：配送异常需要核对，请等待我确认。",
                                "create_ticket",
                            )
                            tid = (await confirm(cid, ticket))["ticket_id"]
                            operator_action(
                                kind="assign_ticket", ticket_id=tid, assignee_id="operator-a"
                            )
                            operator_action(
                                kind="reply_ticket", ticket_id=tid, message="已核对模拟配送记录。"
                            )
                            operator_action(
                                kind="transition_ticket", ticket_id=tid, status="resolved"
                            )
                            record["ticket_id"] = tid
                        else:
                            original = business.order(A, oid)
                            draft = await turn(
                                cid,
                                channel,
                                label + "/cancel",
                                "为订单"
                                + oid
                                + "生成取消及关联整单退款草案，等待运营审批及客户确认。",
                                "cancel_order",
                            )
                            assert draft["status"] == "pending_approval"
                            assert business.order(A, oid) == original
                            rejected = await service.confirm_action(
                                A,
                                cid,
                                ActionConfirmation(
                                    client_request_id=str(uuid4()),
                                    action_id=draft["action_id"],
                                    expected_version=draft["version"],
                                    confirm=True,
                                ),
                            )
                            assert (
                                rejected["status"] == "failed"
                                and rejected["result"]["reason"] == "approval_required"
                            )
                            assert business.order(A, oid) == original
                            draft = business.review(
                                OP,
                                draft["action_id"],
                                Approval(
                                    client_request_id=str(uuid4()),
                                    expected_version=draft["version"],
                                    decision="approve",
                                ),
                            )
                            assert business.order(A, oid) == original
                            await confirm(cid, draft)
                            sim(oid, "refund_succeeded")
                            result = business.order(A, oid)
                            assert (
                                result["status"] == "cancelled"
                                and result["refunded_amount"] == result["paid_amount"]
                            )
                        record.update(
                            status="PASS",
                            final_order=result,
                            conversation_id=cid,
                            model_steps=len(report["cases"]) - start,
                        )
                    except Exception as error:
                        record.update(status="FAILED", error=str(error)[:200])
                    save()
                    print(json.dumps({"scenario": label, "status": record["status"]}), flush=True)
        report["status"] = (
            "PASS" if all(c["status"] == "PASS" for c in report["scenarios"]) else "FAILED"
        )
        report["after_budget"] = ModelStore(raw).status(A.tenant_id + ":" + A.subject)
    finally:
        await service.close()
        await resources.close()
        engine.dispose()
        raw.dispose()
        assert schema.startswith("v2_test_") and len(schema) == 40
        with control.begin() as conn:
            conn.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        control.dispose()
        report["cleaned"] = True
        save()
        print(json.dumps({"evidence": str(path), "status": report["status"]}), flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--approved", action="store_true")
    p.add_argument("--channel", choices=["all", "deepseek", "chatgpt", "qwen"], default="all")
    p.add_argument("--scenario", choices=["all", "delivery", "cancel"], default="all")
    p.add_argument("--family", choices=["all", "ecommerce", "food"], default="all")
    args = p.parse_args()
    if not args.approved:
        raise SystemExit("explicit_live_approval_required")
    raise SystemExit(
        asyncio.run(
            main(
                ("deepseek", "chatgpt", "qwen") if args.channel == "all" else (args.channel,),
                ("delivery", "cancel") if args.scenario == "all" else (args.scenario,),
                ("ecommerce", "food") if args.family == "all" else (args.family,),
            )
        )
    )
