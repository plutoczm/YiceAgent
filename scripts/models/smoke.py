"""Real per-channel smoke: schema intent, read-only native tool, and grounded RAG."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from uuid import uuid4  # noqa: E402

from pydantic import Field  # noqa: E402
from sqlalchemy import text  # noqa: E402

from yice_agent.business.bootstrap import provision, seed  # noqa: E402
from yice_agent.business.contracts import DRAFT  # noqa: E402
from yice_agent.business.service import BusinessService  # noqa: E402
from yice_agent.config import RUNTIME, Settings  # noqa: E402
from yice_agent.contracts import Closed, GenerationRequest, Message, ToolDefinition  # noqa: E402
from yice_agent.database import make_engine  # noqa: E402
from yice_agent.identity import PERSONAS  # noqa: E402
from yice_agent.inference import Inference  # noqa: E402
from yice_agent.knowledge.answer import answer  # noqa: E402
from yice_agent.knowledge.retrieval import Retrieval  # noqa: E402
from yice_agent.knowledge.store import Store  # noqa: E402
from yice_agent.knowledge.vector import Vectors  # noqa: E402
from yice_agent.model_store import ModelStore  # noqa: E402
from yice_agent.models.gateway import Gateway, Intent  # noqa: E402


class Lookup(Closed):
    order_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{0,63}$")


async def run(args):
    if not args.approved:
        raise ValueError("explicit_model_access_approval_required")
    settings = Settings.load()
    if args.channel == "deepseek" and not args.paid_approved:
        raise ValueError("explicit_paid_approval_required")
    engine = make_engine(settings)
    schema = "v2_test_" + uuid4().hex
    with engine.begin() as connection:
        assert connection.exec_driver_sql(
            "SELECT owner FROM public.schema_owner"
        ).scalars().all() == ["workflowops-v2"]
        connection.execute(text('CREATE SCHEMA "' + schema + '"'))
    domain = make_engine(settings, schema)
    provision(domain, schema)
    seed(domain, schema)
    inference = Inference(settings)
    gateway = Gateway(settings, ModelStore(engine), inference)
    actor = PERSONAS["customer-a1"]
    scope = actor.tenant_id + ":" + actor.subject
    evidence = {"channel": args.channel, "tests": [], "real_calls": True}
    try:
        retrieval = Retrieval(Store(engine), Vectors(settings), inference)
        found = await retrieval.search(actor, "电商发货以后可以直接取消订单吗？")
        assert found["status"] == "grounded"
        reply = await answer(
            gateway, args.channel, scope, "电商发货以后可以直接取消订单吗？", found
        )
        assert reply["status"] == "answered" and reply["citations"]
        evidence["tests"].append(
            {
                "case": "grounded_policy",
                "model_id": reply["model_id"],
                "usage": reply["usage"],
                "answer": reply["answer"],
                "citations": reply["citations"],
            }
        )

        request = GenerationRequest(
            messages=[
                Message(
                    role="system",
                    content="Classify user intent. Missing order ID requires clarification. Return JSON matching the schema; never authorize business writes.",
                ),
                Message(role="user", content="我要取消订单，但是没有提供是哪一个订单"),
            ],
            output_schema=Intent.model_json_schema(),
        )
        result = await gateway.generate(args.channel, request, scope)
        intent = Intent.model_validate_json(result.message.content or "")
        assert (
            intent.needs_clarification
            and intent.order_id is None
            and intent.intent in {"cancel", "clarify"}
        )
        evidence["tests"].append(
            {
                "case": "intent_clarification",
                "model_id": result.model_id,
                "usage": result.usage.model_dump(),
                "classification": intent.model_dump(),
            }
        )

        lookup = ToolDefinition(
            name="order_lookup",
            description="Look up the caller's own order. Read-only. Must use this tool for current facts.",
            parameters=Lookup.model_json_schema(),
        )
        messages = [
            Message(
                role="system",
                content="Use order_lookup to obtain current order state. Do not invent order facts or other tools.",
            ),
            Message(
                role="user", content="请调用order_lookup查询订单ECO-A-01，然后告诉我它的状态。"
            ),
        ]
        request = GenerationRequest(messages=messages, tools=[lookup])
        result = await gateway.generate(args.channel, request, scope)
        assert len(result.message.tool_calls) == 1
        call = result.message.tool_calls[0]
        assert call.name == "order_lookup"
        parameters = Lookup.model_validate(call.arguments)
        assert parameters.order_id == "ECO-A-01"
        data = BusinessService(domain).order(actor, parameters.order_id)
        messages.extend(
            [
                result.message,
                Message(
                    role="tool", content=json.dumps(data, ensure_ascii=False), tool_call_id=call.id
                ),
            ]
        )
        follow = await gateway.generate(
            args.channel, GenerationRequest(messages=messages, tools=[lookup]), scope
        )
        assert follow.finish_reason == "stop" and not follow.message.tool_calls
        evidence["tests"].append(
            {
                "case": "native_tool_and_result",
                "model_id": result.model_id,
                "tool": call.model_dump(),
                "order_state": data["status"],
                "usage": [result.usage.model_dump(), follow.usage.model_dump()],
            }
        )

        class CartRequest(Closed):
            product_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{0,63}$")
            quantity: int = Field(ge=1, le=20)

        draft_tool = ToolDefinition(
            name="propose_order",
            description="Create a human-confirmable draft only; never place or pay an order.",
            parameters=CartRequest.model_json_schema(),
        )
        response = await gateway.generate(
            args.channel,
            GenerationRequest(
                messages=[
                    Message(
                        role="system",
                        content="Use propose_order with the exact requested product and quantity; do not authorize or confirm the draft.",
                    ),
                    Message(
                        role="user",
                        content="请生成1个ECO-A-CABLE商品的下单草案，需要我确认后才能下单。",
                    ),
                ],
                tools=[draft_tool],
            ),
            scope,
        )
        assert (
            len(response.message.tool_calls) == 1
            and response.message.tool_calls[0].name == "propose_order"
        )
        cart = CartRequest.model_validate(response.message.tool_calls[0].arguments)
        assert cart.product_id == "ECO-A-CABLE" and cart.quantity == 1
        body = DRAFT.validate_python(
            {
                "kind": "place_order",
                "client_request_id": str(uuid4()),
                "order_type": "ecommerce",
                "items": [{"product_id": cart.product_id, "quantity": cart.quantity}],
                "note": "",
            }
        )
        business = BusinessService(domain)
        before = len(business.orders(actor))
        draft = business.propose(actor, body)
        assert draft["status"] == "pending_confirmation"
        assert len(business.orders(actor)) == before
        evidence["tests"].append(
            {
                "case": "human_confirmable_business_draft",
                "model_id": response.model_id,
                "draft_id": draft["action_id"],
                "state": draft["status"],
                "business_orders_unchanged": True,
                "scope": "isolated_validation",
                "usage": response.usage.model_dump(),
            }
        )
        evidence["status"] = "PASS"
        (RUNTIME / (args.channel + "-model-smoke.json")).write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
    finally:
        await gateway.close()
        await inference.close()
        domain.dispose()
        with engine.begin() as connection:
            assert schema.startswith("v2_test_") and len(schema) == 40
            connection.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=["qwen", "deepseek", "chatgpt"], required=True)
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--paid-approved", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(run(args))
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
