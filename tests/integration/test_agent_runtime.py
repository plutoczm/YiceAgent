"""Real PG handwritten bounded ReAct tests; generated text is explicitly fake, not quality evidence."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from workflowops.agent.contracts import ConversationCreate, TurnRequest
from workflowops.agent.runtime import AgentService
from workflowops.agent.store import AgentStore, Base, Event, Snapshot
from workflowops.config import Settings
from workflowops.contracts import GenerationResult, Message, ToolCall, Usage
from workflowops.database import make_engine
from workflowops.identity import PERSONAS

pytestmark = pytest.mark.integration
A = PERSONAS["customer-a1"]


def response(value, calls=None):
    if isinstance(value, dict) and "decision" in value:
        value = {"action": "final", "calls": [], "final": value}
    return GenerationResult(
        message=Message(
            role="assistant",
            content=json.dumps(value, ensure_ascii=False) if value is not None else None,
            tool_calls=calls or [],
        ),
        model_id="fake-model",
        finish_reason="tool_calls" if calls else "stop",
        usage=Usage(input_tokens=10, output_tokens=10, source="provider"),
    )


class FakeGateway:
    def __init__(self, answers=None, pause=False):
        self.answers = list(answers or [])
        self.pause = pause
        self.calls = 0

    async def generate(self, channel, request, scope):
        self.calls += 1
        if self.pause:
            await asyncio.sleep(30)
        if request.output_schema and request.output_schema.get("title") == "Analysis":
            return response(
                {
                    "intent": "ticket",
                    "needs_clarification": False,
                    "candidates": [],
                    "correction": False,
                }
            )
        if request.output_schema and request.output_schema.get("title") == "Intent":
            return response({"intent": "ticket", "needs_clarification": False})
        if request.output_schema and request.output_schema.get("title") == "OrderDraftSelection":
            next_reply = self.answers.pop(0)
            args = next_reply.message.tool_calls[0].arguments
            return response({k: v for k, v in args.items() if k != "kind"})
        if request.output_schema and request.output_schema.get("title") == "SummaryOutput":
            value = json.loads(request.messages[-1].content)
            return response(
                {
                    "goals": [],
                    "constraints": [
                        {k: v for k, v in q.items() if k in {"message_id", "quote"}}
                        for q in value["required"]
                    ],
                    "corrections": [],
                    "confirmed": [],
                    "unresolved": [],
                }
            )
        if self.answers:
            return self.answers.pop(0)
        return response(
            {"decision": "respond", "reply": "收到。", "quotes": [], "source_quotes": []}
        )

    async def close(self):
        pass


@pytest.fixture
def environment(business):
    domain, schema = business
    control = make_engine(Settings.load(), schema)
    engine = control.execution_options(schema_translate_map={"workflowops_agent": schema})
    with engine.begin() as c:
        Base.metadata.create_all(c)
    store = AgentStore(engine)
    resources = SimpleNamespace(engine=domain.engine, inference=SimpleNamespace())
    services = []

    def create(gateway):
        service = AgentService(domain, resources, store, lambda: gateway)

        async def capabilities(channel, window):
            pass

        service.capabilities = capabilities
        services.append(service)
        return service

    yield create, store, domain
    engine.dispose()
    control.dispose()


async def completed(service, cid, text):
    r = await service.submit(A, cid, TurnRequest(client_request_id=str(uuid4()), text=text))
    task = service.tasks[r["id"]]
    await task
    return service.store.run(A, r["id"])


def test_duplicate_requests_dispatch_once_and_cancel_before_worker(environment):
    create, store, _ = environment
    gateway = FakeGateway()
    service = create(gateway)
    c = store.create(A, ConversationCreate())

    async def run():
        body = TurnRequest(client_request_id=str(uuid4()), text="hello")
        first = await service.submit(A, c["id"], body)
        again = await service.submit(A, c["id"], body)
        assert first["id"] == again["id"]
        await service.cancel(A, first["id"])
        assert store.run(A, first["id"])["status"] == "cancelled"
        assert store.conversation(A, c["id"])["active_run"] is None
        assert gateway.calls == 0

    asyncio.run(run())


def test_mid_model_cancel_preserves_history_and_record(environment):
    create, store, _ = environment
    service = create(FakeGateway(pause=True))
    c = store.create(A, ConversationCreate())

    async def run():
        r = await service.submit(
            A, c["id"], TurnRequest(client_request_id=str(uuid4()), text="原始请求")
        )
        await asyncio.sleep(0.05)
        await service.cancel(A, r["id"])
        assert store.run(A, r["id"])["status"] == "cancelled"
        assert store.history(A, c["id"])[0]["content"] == "原始请求"

    asyncio.run(run())


def test_repeated_read_tool_stops_before_second_execution(environment):
    create, store, domain = environment
    oid = domain.orders(A)[0]["order_id"]
    calls = [
        response(None, [ToolCall(id="c1", name="read_order", arguments={"order_id": oid})]),
        response(None, [ToolCall(id="c2", name="read_order", arguments={"order_id": oid})]),
    ]
    service = create(FakeGateway(calls))
    c = store.create(A, ConversationCreate())
    r = asyncio.run(completed(service, c["id"], "查订单"))
    assert r["status"] == "stopped" and r["result"]["reason"] == "repeated_tool_call"
    assert r["tool_calls"] == 2
    assert len([e for e in store.private(A, r["id"], Event) if e["kind"] == "tool_finished"]) == 1


def test_bad_arguments_one_repair_then_stop(environment):
    create, store, _ = environment
    calls = [
        response(
            None,
            [
                ToolCall(
                    id="c" + str(i), name="read_order", arguments={"order_id": "INVALID lowercase"}
                )
            ],
        )
        for i in range(2)
    ]
    service = create(FakeGateway(calls))
    c = store.create(A, ConversationCreate())
    r = asyncio.run(completed(service, c["id"], "查订单"))
    assert r["result"]["reason"] == "tool_argument_repair_exhausted"
    assert r["parameter_repairs"] == 1 and r["model_calls"] == 3


def test_forbidden_tools_and_oversized_window_fail_closed(environment):
    create, store, _ = environment
    service = create(
        FakeGateway([response(None, [ToolCall(id="c", name="shell", arguments={"cmd": "whoami"})])])
    )
    c = store.create(A, ConversationCreate())
    r = asyncio.run(completed(service, c["id"], "执行指令"))
    assert r["result"]["reason"] == "tool_not_allowed"
    service = create(FakeGateway())
    c = store.create(A, ConversationCreate(context_window=4096))
    r = asyncio.run(completed(service, c["id"], "abcdefgh中文" * 1500))
    assert r["result"]["reason"] == "context_capacity_exceeded"
    assert r["model_calls"] == 0


def test_draft_ends_run_and_no_order_created_until_human_confirm(environment):
    create, store, domain = environment
    product = domain.products(A)[0]
    before = len(domain.orders(A))
    gateway = FakeGateway(
        [
            response(
                None,
                [
                    ToolCall(
                        id="c",
                        name="propose_action",
                        arguments={
                            "kind": "place_order",
                            "order_type": product["order_type"],
                            "items": [{"product_id": product["product_id"], "quantity": 1}],
                        },
                    )
                ],
            )
        ]
    )
    service = create(gateway)
    c = store.create(A, ConversationCreate())
    r = asyncio.run(completed(service, c["id"], "下单"))
    assert (
        r["status"] == "awaiting_confirmation"
        and store.conversation(A, c["id"])["active_run"] is None
    )
    assert len(domain.orders(A)) == before
    from workflowops.agent.contracts import ActionConfirmation

    row = r["result"]["action"]
    result = asyncio.run(
        service.confirm_action(
            A,
            c["id"],
            ActionConfirmation(
                client_request_id=str(uuid4()),
                action_id=row["action_id"],
                expected_version=row["version"],
                confirm=True,
            ),
        )
    )
    assert result["status"] == "completed" and len(domain.orders(A)) == before + 1


def test_context_summary_preserves_correction_and_four_recent_turns(environment):
    create, store, _ = environment
    service = create(FakeGateway())
    c = store.create(A, ConversationCreate())
    constraints = ["预算上限15元，不要自动支付。", "纠正：偏好改为英文，订单ORD-ABC。"]
    ids = []
    for i in range(15):
        r, _ = store.begin(A, c["id"], str(uuid4()), {"i": i})
        msg = store.append(
            r["id"],
            {"role": "user", "content": constraints[i] if i < 2 else "闲聊天气颜色音乐风景。" * 35},
        )
        store.append(r["id"], {"role": "assistant", "content": "收到。"})
        store.finish(r["id"], "completed")
        ids.append(msg["id"])
    run, _ = store.begin(A, c["id"], str(uuid4()), {})
    store.append(run["id"], {"role": "user", "content": "请回忆最早约束。"})

    async def summarize(request, purpose):
        return await service.gateway_factory().generate("qwen", request, "fake")

    request = asyncio.run(service.context.assemble(A, run, generate_summary=summarize, manual=True))
    snap = store.private(A, run["id"], Snapshot)[-1]
    summary = store.summary(A, c["id"])
    assert ids[-4:] == [mid for mid in ids if mid in snap["included_message_ids"]]
    for value in constraints:
        assert value in json.dumps(summary["content"], ensure_ascii=False)
        assert value in json.dumps(snap["messages"], ensure_ascii=False)
    assert len(store.history(A, c["id"])) == 31
    assert snap["after_tokens"] < snap["before_tokens"]
    same = asyncio.run(service.context.assemble(A, run, generate_summary=summarize, manual=True))
    assert store.summary(A, c["id"])["version"] == summary["version"]
    assert request.messages and same.messages


def test_bad_summary_is_not_saved_and_raw_fallback_kept(environment):
    create, store, _ = environment
    service = create(FakeGateway())
    c = store.create(A, ConversationCreate())
    for i in range(7):
        r, _ = store.begin(A, c["id"], str(uuid4()), {})
        store.append(
            r["id"], {"role": "user", "content": "预算上限15元。" if i == 0 else "无关闲聊。"}
        )
        store.append(r["id"], {"role": "assistant", "content": "收到"})
        store.finish(r["id"], "completed")
    run, _ = store.begin(A, c["id"], str(uuid4()), {})

    async def invalid(request, purpose):
        return response(
            {"goals": [], "constraints": [], "corrections": [], "confirmed": [], "unresolved": []}
        )

    req = asyncio.run(service.context.assemble(A, run, generate_summary=invalid, manual=True))
    assert store.summary(A, c["id"]) is None
    assert any("预算上限15元" in (m.content or "") for m in req.messages)
    assert store.private(A, run["id"], Event)[0]["kind"] == "summary_rejected"


def test_http_owner_csrf_duplicate_run_and_private_snapshot(environment):
    import httpx

    from workflowops.app import create_app

    create, store, domain = environment
    service = create(FakeGateway())
    app = create_app(domain, agent=service)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            assert (await client.get("/api/v2/conversations")).status_code == 401
            session = (
                await client.post("/api/v2/demo/session", json={"persona": "customer-a1"})
            ).json()
            headers = {"X-WorkflowOps-CSRF": session["csrf_token"]}
            assert (await client.post("/api/v2/conversations", json={})).status_code == 403
            c = (await client.post("/api/v2/conversations", json={}, headers=headers)).json()
            body = {"text": "hello", "client_request_id": str(uuid4())}
            first = (
                await client.post(
                    "/api/v2/conversations/" + c["id"] + "/turns", json=body, headers=headers
                )
            ).json()
            again = (
                await client.post(
                    "/api/v2/conversations/" + c["id"] + "/turns", json=body, headers=headers
                )
            ).json()
            assert first["id"] == again["id"]
            # Immediate handlers can finish before the duplicate HTTP response arrives.
            # Completion is durable; the in-memory task registry only tracks active work.
            task = service.tasks.get(first["id"])
            if task is not None:
                await task
            assert (await client.get("/api/v2/runs/" + first["id"])).json()["status"] == "completed"
            snapshots = (await client.get("/api/v2/runs/" + first["id"] + "/context")).json()
            assert snapshots and snapshots[-1]["source"].endswith("estimate")
            stream = await client.get("/api/v2/runs/" + first["id"] + "/stream")
            assert "event: final" in stream.text and "fake-model" in stream.text
            await client.post("/api/v2/demo/session", json={"persona": "customer-a2"})
            assert (await client.get("/api/v2/conversations")).json() == []
            private = await client.get("/api/v2/runs/" + first["id"] + "/context")
            assert private.status_code >= 400

    asyncio.run(scenario())


def test_six_model_attempt_limit_stops_a_non_repeating_tool_loop(environment):
    create, store, domain = environment
    calls = [
        response(
            None, [ToolCall(id="page" + str(i), name="list_products", arguments={"offset": i})]
        )
        for i in range(7)
    ]
    service = create(FakeGateway(calls))
    c = store.create(A, ConversationCreate(context_window=16384))
    r = asyncio.run(completed(service, c["id"], "查看商品"))
    assert r["model_calls"] == 6 and r["tool_calls"] <= 8
    assert r["result"]["reason"] == "model_call_budget_exceeded"


def test_eight_tool_calls_limit_even_in_batched_native_responses(environment):
    create, store, domain = environment
    rows = domain.orders(A)
    first = [
        ToolCall(id="one" + str(i), name="read_order", arguments={"order_id": r["order_id"]})
        for i, r in enumerate(rows[:5])
    ]
    second = [
        ToolCall(id="two" + str(i), name="list_products", arguments={"offset": i}) for i in range(5)
    ]
    service = create(FakeGateway([response(None, first), response(None, second)]))
    c = store.create(A, ConversationCreate(context_window=16384))
    r = asyncio.run(completed(service, c["id"], "查看订单和商品"))
    assert r["tool_calls"] == 8 and r["result"]["reason"] == "tool_call_budget_exceeded"


def test_timeout_and_model_error_persist_terminal_status(environment):
    from workflowops.model_store import ModelError

    create, store, _ = environment

    class Failing(FakeGateway):
        async def generate(self, channel, request, scope):
            raise ModelError("provider_timeout")

    service = create(Failing())
    c = store.create(A, ConversationCreate())
    r = asyncio.run(completed(service, c["id"], "hello"))
    assert r["status"] == "stopped" and r["result"]["reason"] == "provider_timeout"
    assert store.conversation(A, c["id"])["active_run"] is None


def test_recall_rechecks_authority_if_memory_edited_during_generation(environment):
    from workflowops.agent.contracts import CandidateInput, MemoryConfirm, MemoryEdit
    from workflowops.agent.memory import model_view

    create, store, _ = environment
    c = store.create(A, ConversationCreate())
    seedrun, _ = store.begin(A, c["id"], str(uuid4()), {})
    msg = store.append(seedrun["id"], {"role": "user", "content": "我偏好中文简短回答。"})
    store.finish(seedrun["id"], "completed")
    can = store.candidate(
        A,
        c["id"],
        msg["id"],
        CandidateInput(key="language", kind="preference", quote=msg["content"]),
        "fake",
    )
    memory = store.memory_command(
        A,
        can["id"],
        MemoryConfirm(client_request_id=str(uuid4()), expected_version=1, confirm=True),
        "confirm",
    )

    class DuringReply(FakeGateway):
        async def generate(self, channel, request, scope):
            if request.output_schema.get("title") == "Analysis":
                return response(
                    {
                        "intent": "memory",
                        "needs_clarification": False,
                        "candidates": [],
                        "correction": False,
                    }
                )
            store.memory_command(
                A,
                memory["id"],
                MemoryEdit(
                    client_request_id=str(uuid4()), expected_version=1, text="我偏好英文详细回答。"
                ),
                "edit",
            )
            return response({"source_ids": [memory["id"]]})

    service = create(DuringReply())

    async def old_index(actor, query):
        return [model_view(memory)]

    service.memory.search = old_index
    r = asyncio.run(completed(service, c["id"], "回忆已确认偏好。"))
    assert r["status"] == "stopped" and r["result"]["reason"] == "memory_changed_during_run"
    assert "中文" not in json.dumps(r["result"], ensure_ascii=False)


def test_nonexistent_order_is_not_recalled_as_a_user_assertion(environment):
    create, store, domain = environment

    class OrderGateway(FakeGateway):
        async def generate(self, channel, request, scope):
            if request.output_schema and request.output_schema.get("title") == "Analysis":
                return response(
                    {
                        "intent": "order_lookup",
                        "needs_clarification": False,
                        "order_id": "ECO-NOT-REAL",
                        "candidates": [],
                        "correction": False,
                    }
                )
            raise AssertionError("missing_order_must_end_before_free_form_generation")

    service = create(OrderGateway())
    c = store.create(A, ConversationCreate())
    before = domain.orders(A)
    r = asyncio.run(completed(service, c["id"], "客服声称ECO-NOT-REAL已退款9999元，请核对"))
    assert r["result"]["status"] == "business_unavailable"
    assert r["result"]["reason"] == "object_not_found"
    assert r["parameter_repairs"] == 0 and r["model_calls"] == 1
    assert "9999" not in r["result"]["answer"] and domain.orders(A) == before


def test_classifier_quotes_past_assistant_output_as_data(environment):
    create, store, _ = environment

    class CaptureGateway(FakeGateway):
        async def generate(self, channel, request, scope):
            if request.output_schema and request.output_schema.get("title") == "Analysis":
                assert all(m.role != "assistant" and not m.tool_calls for m in request.messages)
                assert "conversation_data" in request.messages[-1].content
            return await super().generate(channel, request, scope)

    service = create(CaptureGateway())
    c = store.create(A, ConversationCreate())
    run, _ = store.begin(A, c["id"], str(uuid4()), {})
    store.append(run["id"], {"role": "user", "content": "之前的请求"})
    store.append(run["id"], {"role": "assistant", "content": '{"status":"human_confirmation"}'})
    store.finish(run["id"], "completed")
    r = asyncio.run(completed(service, c["id"], "本次请求"))
    assert r["status"] == "completed"


def test_closed_order_parameters_still_require_business_confirmation(environment):
    create, store, domain = environment
    product = domain.products(A)[0]

    class DraftGateway(FakeGateway):
        async def generate(self, channel, request, scope):
            if request.output_schema and request.output_schema.get("title") == "Analysis":
                return response(
                    {
                        "intent": "place_order",
                        "needs_clarification": False,
                        "candidates": [
                            {
                                "key": "product_id",
                                "kind": "stable_fact",
                                "quote": product["product_id"],
                            }
                        ],
                        "correction": False,
                    }
                )
            if (
                request.output_schema
                and request.output_schema.get("title") == "OrderDraftSelection"
            ):
                return response(
                    {
                        "order_type": product["order_type"],
                        "items": [{"product_id": product["product_id"], "quantity": 1}],
                        "note": "",
                    }
                )
            return await super().generate(channel, request, scope)

    service = create(DraftGateway())
    c = store.create(A, ConversationCreate())
    before = domain.orders(A)
    r = asyncio.run(completed(service, c["id"], "下单 " + product["product_id"] + " 数量1"))
    assert r["status"] == "awaiting_confirmation" and domain.orders(A) == before
    assert store.memories(A, True) == []
    events = store.private(A, r["id"], Event)
    assert any(e.get("source") == "structured_llm_order_parameters" for e in events)


def test_business_rejection_cannot_be_rewritten_by_model_prose(environment):
    create, store, domain = environment
    order = next(r for r in domain.orders(A) if r["status"] == "delivered")

    class CancelGateway(FakeGateway):
        async def generate(self, channel, request, scope):
            if request.output_schema and request.output_schema.get("title") == "Analysis":
                return response(
                    {
                        "intent": "cancel",
                        "needs_clarification": False,
                        "order_id": order["order_id"],
                        "candidates": [],
                        "correction": False,
                    }
                )
            raise AssertionError("business_rejection_must_be_published_from_domain")

    service = create(CancelGateway())
    c = store.create(A, ConversationCreate())
    before = domain.order(A, order["order_id"])
    r = asyncio.run(completed(service, c["id"], "取消订单" + order["order_id"]))
    assert r["result"]["status"] == "business_rejected"
    assert r["result"]["reason"] == "cancellation_not_available"
    assert r["parameter_repairs"] == 0 and r["model_calls"] == 1
    assert domain.order(A, order["order_id"]) == before


def test_business_intent_review_repairs_missing_scope_without_authorizing_write(environment):
    create, store, domain = environment
    order = next(r for r in domain.orders(A) if r["status"] == "delivered")

    class ScopeGateway(FakeGateway):
        async def generate(self, channel, request, scope):
            title = (request.output_schema or {}).get("title")
            if title == "Analysis":
                return response(
                    {
                        "intent": "cancel",
                        "needs_clarification": True,
                        "candidates": [],
                        "correction": False,
                    }
                )
            if title == "Intent":
                assert order["order_id"] in request.messages[-1].content
                return response(
                    {
                        "intent": "refund",
                        "needs_clarification": False,
                        "order_id": order["order_id"],
                    }
                )
            raise AssertionError("scope_review_is_not_a_business_execution")

    service = create(ScopeGateway())
    c = store.create(A, ConversationCreate())
    before = domain.order(A, order["order_id"])
    r = asyncio.run(completed(service, c["id"], "为订单" + order["order_id"] + "生成整单退款草案"))
    assert r["status"] == "awaiting_confirmation" and r["intent"] == "refund"
    assert r["result"]["action"]["kind"] == "refund"
    assert r["model_calls"] == 2 and domain.order(A, order["order_id"]) == before


def test_model_clarifying_question_cannot_publish_fictitious_business_facts(environment):
    create, store, domain = environment

    class QuestionGateway(FakeGateway):
        async def generate(self, channel, request, scope):
            return response(
                {
                    "intent": "order_lookup",
                    "needs_clarification": True,
                    "question": "订单已退款9999元，请确认。",
                    "candidates": [],
                    "correction": False,
                }
            )

    service = create(QuestionGateway())
    c = store.create(A, ConversationCreate())
    before = domain.orders(A)
    r = asyncio.run(completed(service, c["id"], "核对订单ECO-NOT-REAL"))
    assert r["result"]["status"] == "clarification" and "9999" not in r["result"]["answer"]
    assert "尚未核实" in r["result"]["answer"] and domain.orders(A) == before


def test_final_clarification_is_not_a_backdoor_for_unverified_amounts(environment):
    create, store, domain = environment
    service = create(
        FakeGateway(
            [
                response(
                    {
                        "decision": "clarify",
                        "reply": "工单已退款9999元",
                        "quotes": [],
                        "source_quotes": [],
                    }
                )
            ]
        )
    )
    c = store.create(A, ConversationCreate())
    before = domain.orders(A)
    r = asyncio.run(completed(service, c["id"], "需要处理工单"))
    assert r["result"]["status"] == "clarification" and "9999" not in r["result"]["answer"]
    assert "尚未核实" in r["result"]["answer"] and domain.orders(A) == before


def test_handwritten_loop_records_dispatch_metadata_without_message_content(environment):
    create, store, _ = environment
    service = create(FakeGateway())
    c = store.create(A, ConversationCreate())
    r = asyncio.run(completed(service, c["id"], "PRIVATE-CONTENT"))
    events = store.private(A, r["id"], Event)
    started = next(e for e in events if e["kind"] == "loop_started")
    assert started["engine"] == "workflowops-react-v1" and started["max_steps"] == 24
    transitions = [e for e in events if e["kind"] == "loop_transition"]
    assert [(e["from"], e["to"]) for e in transitions] == [
        ("analyse", "decide"),
        ("decide", "finish"),
    ]
    assert all("PRIVATE-CONTENT" not in json.dumps(e) for e in transitions)
    assert all(e["model_calls"] >= 1 and e["tool_calls"] == 0 for e in transitions)


def test_duplicate_after_fast_completion_returns_durable_run_without_new_model_call(environment):
    create, store, _ = environment
    gateway = FakeGateway()
    service = create(gateway)
    c = store.create(A, ConversationCreate())

    async def scenario():
        body = TurnRequest(client_request_id=str(uuid4()), text="one request")
        first = await service.submit(A, c["id"], body)
        await service.tasks[first["id"]]
        await asyncio.sleep(0)
        before = gateway.calls
        again = await service.submit(A, c["id"], body)
        assert again["id"] == first["id"]
        assert store.run(A, first["id"])["status"] == "completed"
        assert service.tasks.get(first["id"]) is None
        assert gateway.calls == before

    asyncio.run(scenario())
