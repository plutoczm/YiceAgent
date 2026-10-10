"""Provider wire/budget/citation safety; mocked transports are not model-quality evidence."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from yice_agent.config import Settings
from yice_agent.contracts import GenerationRequest, Message, ToolCall, ToolDefinition
from yice_agent.knowledge.answer import Quote, Selection, publish
from yice_agent.knowledge.retrieval import bm25, rrf
from yice_agent.model_store import ModelError, cost
from yice_agent.models.http import HttpAdapter, validate_request


def settings(**extra):
    return Settings(database_password="p" * 48, qdrant_key="q" * 48, **extra)


def request(tools=None, schema=None):
    return GenerationRequest(
        messages=[Message(role="user", content="查询订单ECO-A-01")],
        tools=tools or [],
        output_schema=schema,
    )


def tool():
    return ToolDefinition(
        name="order_lookup",
        description="查询当前用户自己的订单",
        parameters={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    )


def test_qwen_native_controls_and_usage():
    def server(req):
        value = json.loads(req.content)
        assert req.url == "http://127.0.0.1:11434/api/chat"
        assert value["think"] is False and value["stream"] is False
        assert value["options"]["num_ctx"] == 8192 and value["options"]["num_predict"] == 1536
        assert value["format"] == {"type": "object"}
        assert "tools" in value
        return httpx.Response(
            200,
            json={
                "model": "qwen3:8b",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "thinking": "PRIVATE INTERNAL THINKING",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "order_lookup",
                                "arguments": {"order_id": "ECO-A-01"},
                            }
                        }
                    ],
                },
                "prompt_eval_count": 200,
                "eval_count": 30,
                "done_reason": "stop",
            },
        )

    adapter = HttpAdapter(settings(), "qwen", transport=httpx.MockTransport(server))
    result = asyncio.run(adapter.generate(request([tool()], {"type": "object"})))
    assert result.finish_reason == "tool_calls"
    assert result.message.tool_calls[0].arguments == {"order_id": "ECO-A-01"}
    assert "PRIVATE" not in result.model_dump_json()
    assert result.usage.source == "provider" and result.usage.input_tokens == 200


def test_deepseek_non_thinking_and_native_tool_pair():
    def server(req):
        value = json.loads(req.content)
        assert value["model"] == "deepseek-flash"
        assert value["thinking"] == {"type": "disabled"}
        assert value["max_tokens"] == 1536
        assert value["messages"][-1]["tool_call_id"] == "call-1"
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "已查询",
                            "reasoning_content": "PRIVATE",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 10},
            },
        )

    messages = [
        Message(role="user", content="查询"),
        Message(
            role="assistant",
            tool_calls=[
                ToolCall(id="call-1", name="order_lookup", arguments={"order_id": "ECO-A-01"})
            ],
        ),
        Message(role="tool", content='{"status":"paid"}', tool_call_id="call-1"),
    ]
    adapter = HttpAdapter(
        settings(deepseek_key="fake-key-local-unit"),
        "deepseek",
        transport=httpx.MockTransport(server),
    )
    result = asyncio.run(adapter.generate(GenerationRequest(messages=messages, tools=[tool()])))
    assert result.message.content == "已查询" and "PRIVATE" not in result.model_dump_json()


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "provider_auth_failed"),
        (429, "provider_quota_unavailable"),
        (500, "provider_request_failed"),
    ],
)
def test_provider_failure_is_explicit_and_does_not_leak(status, code):
    count = 0

    def server(req):
        nonlocal count
        count += 1
        return httpx.Response(status, json={"secret": "PRIVATE_FAILURE_DATA"})

    adapter = HttpAdapter(settings(), "qwen", transport=httpx.MockTransport(server))
    with pytest.raises(ModelError, match=code) as error:
        asyncio.run(adapter.generate(request()))
    assert count == 1 and "PRIVATE" not in str(error.value)


def test_unrequested_tool_is_rejected():
    def server(req):
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "execute_shell", "arguments": {"cmd": "anything"}}}
                    ],
                }
            },
        )

    with pytest.raises(ModelError, match="unrequested_tool"):
        asyncio.run(
            HttpAdapter(settings(), "qwen", transport=httpx.MockTransport(server)).generate(
                request([tool()])
            )
        )


def test_context_overflow_rejected_before_dispatch():
    with pytest.raises(ModelError, match="context_capacity_exceeded"):
        validate_request(
            GenerationRequest(
                messages=[Message(role="user", content="中文" * 2000)], context_window=4096
            )
        )


def test_cny_reservation_requires_rates_and_rounds_up():
    with pytest.raises(ModelError, match="reliable_rates_required"):
        cost(8192, 1536, settings())
    assert cost(
        8192, 1536, settings(input_price_cny_per_million="2", output_price_cny_per_million="8")
    ) == Decimal("0.028672")
    with pytest.raises(ModelError, match="reliable_rates_required"):
        cost(100, 100, settings(input_price_cny_per_million="0", output_price_cny_per_million="8"))


def evidence():
    return [
        {
            "id": "E1",
            "document_id": "demo",
            "title": "电商取消",
            "version": "1",
            "source": "project:demo",
            "sha256": "hash",
            "content": "电商发货前可取消。已发货不可直接取消。",
        }
    ]


def test_exact_citation_publication_and_fabricated_rejection():
    result = publish(
        Selection(decision="answer", quotes=[Quote(evidence_id="E1", quote="电商发货前可取消。")]),
        evidence(),
    )
    assert result["citations"][0]["id"] == "E1"
    for quote in [
        Quote(evidence_id="FAKE", quote="电商发货前可取消。"),
        Quote(evidence_id="E1", quote="已发货也可以取消"),
    ]:
        with pytest.raises(ModelError, match="unverifiable_citation"):
            publish(Selection(decision="answer", quotes=[quote]), evidence())


def test_no_answer_cannot_smuggle_a_claim():
    with pytest.raises(ModelError, match="unverifiable_answer"):
        publish(
            Selection(
                decision="no_answer", quotes=[Quote(evidence_id="E1", quote="电商发货前可取消。")]
            ),
            evidence(),
        )


def test_actual_bm25_and_rrf_have_no_business_scoring_rules():
    rows = [
        {"point_id": "A", "content": "外卖商家接单前取消"},
        {"point_id": "B", "content": "微积分数学定理"},
    ]
    assert bm25("外卖取消", rows)[0] == "A"
    assert rrf(["A", "B"], ["B", "C"]) == ["B", "A", "C"]


def test_credentials_never_enter_model_messages():
    request = GenerationRequest(
        messages=[Message(role="user", content="api_key: sk-abcdefghijklmnopqrstuvwxyz123")]
    )
    with pytest.raises(ModelError, match="credential_in_model_context"):
        validate_request(request)
