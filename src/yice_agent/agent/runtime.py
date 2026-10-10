"""Single-owner handwritten ReAct runs; models propose, business services authorize."""

import asyncio
import re
import time
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
from pydantic import ValidationError

from yice_agent.agent.context import ContextManager, serial
from yice_agent.agent.contracts import (
    Analysis,
    IntentChoice,
    OrderDraftSelection,
    RecallSelection,
    StepOutput,
)
from yice_agent.agent.loop import ENGINE_ID, LoopState, ReActLoop, Transition
from yice_agent.agent.memory import MemoryIndex, model_view
from yice_agent.agent.store import AgentStore, Event, Result
from yice_agent.agent.tools import LoopGuard, Tools
from yice_agent.business.contracts import Confirmation
from yice_agent.business.rules import BusinessError
from yice_agent.business.service import UnknownOutcome
from yice_agent.config import Settings
from yice_agent.contracts import Message, ToolCall
from yice_agent.knowledge.answer import Quote, Selection, publish
from yice_agent.knowledge.vector import Vectors
from yice_agent.model_store import ModelError, ModelStore
from yice_agent.models.gateway import Gateway, Intent
from yice_agent.rpc import WorkerError
from yice_agent.tokens import count_text, estimate

FINAL_INSTRUCTIONS = (
    "需要工具时调用白名单工具。用户表示纠正或偏好时先记录，不要调用业务写入。"
    "不需要工具时只输出这个schema的JSON："
    + "StepOutput"
    + "。需要工具时action=tool，calls列出工具name与arguments，final=null。完成时action=final，calls=[]，final包含答复。知识回答用final.decision=knowledge，quotes包含evidence_id和逐字quote。"
    "总结或回忆用户事实时source_quotes包含message_id和逐字quote，不要改写事实。"
    "普通回复respond；澄清clarify。订单事实仅用本轮read_order结果。回忆当前状态时只引用最新纠正后的对应原文，历史对比才附旧值。"
)


@dataclass
class Execution:
    service: object
    actor: object
    run: dict
    gateway: object
    manual: bool = False
    deadline: float = field(default_factory=lambda: time.monotonic() + 600)
    model_calls: int = 0
    tool_calls: int = 0
    repairs: int = 0
    factor: float = 1.2
    tools: object = None
    analysis: object = None
    memories: list = field(default_factory=list)
    actions: list = field(default_factory=list)

    def check(self):
        if time.monotonic() >= self.deadline:
            raise ModelError("run_deadline_exceeded")

    async def generate(self, request, purpose):
        self.check()
        if self.model_calls >= 6:
            raise ModelError("model_call_budget_exceeded")
        self.model_calls += 1
        request.timeout_seconds = min(
            request.timeout_seconds, max(1, int(self.deadline - time.monotonic()))
        )
        measurement = estimate(request, self.factor)
        from yice_agent.agent.store import Snapshot

        history = self.service.store.history(self.actor, self.run["conversation_id"])
        planned = self.service.store.private(self.actor, self.run["id"], Snapshot)
        prior = planned[-1] if planned else {}
        serialized = serial([m.model_dump(mode="json") for m in request.messages])
        selected = [r["id"] for r in history if r["id"] in serialized]
        if purpose not in {"summary", "intent_review"}:
            selected = prior.get("included_message_ids", selected)
        self.service.store.add_private(
            Snapshot,
            self.run["id"],
            {
                **prior,
                "snapshot_kind": "actual_model_request",
                "purpose": purpose,
                "channel": self.run["channel"],
                "context_window": request.context_window,
                "input_budget": request.context_window - 1536 - 512,
                "output_reserved": 1536,
                "actual_output_limit": request.max_output_tokens,
                "safety_margin": 512,
                **measurement,
                "before_tokens": prior.get("before_tokens", measurement["input_tokens"]),
                "after_tokens": measurement["input_tokens"],
                "reason": prior.get("reason", "call_budget_check"),
                "summary_version": prior.get("summary_version"),
                "included_message_ids": selected,
                "omitted": prior.get("omitted", []),
                "components": [
                    {
                        "role": m.role,
                        "estimated_tokens": count_text(
                            serial(m.model_dump(mode="json")), self.factor
                        ),
                    }
                    for m in request.messages
                ]
                + [
                    {
                        "role": "tool_definitions",
                        "estimated_tokens": count_text(
                            serial([t.model_dump(mode="json") for t in request.tools]), self.factor
                        ),
                    },
                    {
                        "role": "output_schema",
                        "estimated_tokens": count_text(serial(request.output_schema), self.factor),
                    },
                ],
                "messages": [m.model_dump(mode="json") for m in request.messages],
                "tools": [t.model_dump(mode="json") for t in request.tools],
                "output_schema": request.output_schema,
            },
        )
        if measurement["input_tokens"] + 1536 + 512 > request.context_window:
            raise ModelError("context_capacity_exceeded")
        self.service.store.update_run(
            self.run["id"], model_calls=self.model_calls, tool_calls=self.tool_calls
        )
        self.service.store.add_private(
            Event,
            self.run["id"],
            {
                "kind": "model_started",
                "purpose": purpose,
                "number": self.model_calls,
                "input_estimate": measurement,
                "channel": self.run["channel"],
            },
        )
        result = await self.gateway.generate(
            self.run["channel"],
            request,
            self.actor.tenant_id
            + ":"
            + self.actor.subject
            + ":"
            + self.run["conversation_id"]
            + ":"
            + self.run["id"],
        )
        if result.usage.input_tokens is not None and measurement["base_tokens"]:
            self.factor = max(
                self.factor, result.usage.input_tokens / measurement["base_tokens"] * 1.1
            )
        self.run["model_id"] = result.model_id
        self.service.store.update_run(
            self.run["id"], model_id=result.model_id, estimator_factor=self.factor
        )
        self.service.store.add_private(
            Event,
            self.run["id"],
            {
                "kind": "model_finished",
                "purpose": purpose,
                "model_id": result.model_id,
                "usage": result.usage.model_dump(),
                "finish_reason": result.finish_reason,
                "estimated_input": measurement["input_tokens"],
                "actual_input": result.usage.input_tokens,
            },
        )
        self.service.store.add_private(
            Result,
            self.run["id"],
            {
                "kind": "unvalidated_model_output",
                "purpose": purpose,
                "message": result.message.model_dump(mode="json"),
                "model_id": result.model_id,
                "verified_final": False,
            },
        )
        if result.finish_reason in {"length", "other"}:
            raise ModelError("model_response_incomplete")
        return result


async def analyse(state: LoopState, e: Execution):
    store = e.service.store
    e.tools = Tools(e.service, e.actor, e.run, LoopGuard())
    # Fresh authority for confirmation; do not quote an old action status from conversation.
    e.actions = [
        {k: a[k] for k in ("action_id", "status", "version", "confirmed", "human_approved")}
        for a in e.service.actions(e.actor, e.run["conversation_id"])
    ]
    store.update_run(e.run["id"], action_states=e.actions)
    e.memories = [model_view(row) for row in store.memories(e.actor)[:5]]
    if e.manual:
        await e.service.context.assemble(
            e.actor,
            e.run,
            memories=e.memories,
            actions=e.actions,
            generate_summary=e.generate,
            manual=True,
            factor=e.factor,
        )
        rejected = any(
            event["kind"] == "summary_rejected"
            for event in store.private(e.actor, e.run["id"], Event)
        )
        return {
            "finished": True,
            "result": {
                "status": "compression_fallback" if rejected else "compressed",
                "answer": "摘要未通过来源校验；未保存新摘要，保留可容纳的原始上下文及有效状态。"
                if rejected
                else "已核对并整理上下文，原始历史保留。",
            },
        }
    req = await e.service.context.assemble(
        e.actor,
        e.run,
        schema=Analysis.model_json_schema(),
        memories=e.memories,
        actions=e.actions,
        generate_summary=e.generate,
        factor=e.factor,
        instructions="只识别标有current_request的本次请求，不分类旧历史。输出schema JSON。偏好、记住、纠正、回忆约束属于memory；演示政策问题属于knowledge；带明确订单ID的查询属于order_lookup。只有实际业务操作缺少订单/商品/范围才needs_clarification=true，偏好和规则问答不需要订单ID。"
        "task_updates提取本次用户目标、约束、待解决事项，quote只摘本次current_request的连续原文；kind为goal/constraint/unresolved，key为稳定属性名（预算budget_cny、回答语言response_language、代号tracking_code），operation=set。"
        "明确纠正时可引用effective_task_state中的同属性fact id到replaces；无法确定对象时不要替换。已解决需本次原文明确提及旧事项才能operation=resolve。不要提取模型推测、订单/支付状态、权限或业务确认；没有新要求时task_updates=[]。"
        "候选仅摘录当前用户明确陈述的偏好或稳定信息，quote逐字原文，key为稳定英文属性名。"
        "不保存订单状态、模型推测或凭据。只有本次请求明确纠正时correction=true，不能复制旧历史候选。"
        "取消订单并关联整单退款属于cancel，只有单独退款才refund；下单、支付和工单的本次参数不是长期稳定信息，candidates应为空。"
        "例：电商已发货能否取消、请引用规则 -> knowledge,false；查询ECO-A-01 -> order_lookup,false,order_id=ECO-A-01；我偏好中文 -> memory,false；取消我的订单但没ID -> cancel,true；以下无关练习材料，无需操作或复述 -> clarify,false。"
        'JSON格式示例（不是当前请求的分类）：{"intent":"order_lookup","needs_clarification":false,"order_id":"ECO-A-01","question":null,"candidates":[],"correction":false}。必须输出非空JSON对象。',
    )
    # Classification is a separate structured task. Quote past assistant/tool
    # outputs as data instead of presenting other output schemas as few-shot answers.
    req.messages = [
        req.messages[0],
        Message(
            role="user",
            content=serial(
                {
                    "conversation_data": [m.model_dump(mode="json") for m in req.messages[1:]],
                    "task": "Classify only current_request. Return a non-empty JSON object matching Analysis.",
                }
            ),
        ),
    ]
    result = await e.generate(req, "intent")
    e.analysis = Analysis.model_validate_json(result.message.content or "")
    current_text = next(
        r["content"]
        for r in reversed(store.history(e.actor, e.run["conversation_id"]))
        if r["run_id"] == e.run["id"] and r["role"] == "user"
    )
    if (
        e.analysis.intent != "memory"
        and any(
            w in current_text for w in ("回忆", "最早", "最新纠正", "学习代号", "已确认长期记忆")
        )
    ) or "草案" in current_text:
        review_type = Intent if "草案" in current_text else IntentChoice
        review = e.service.context.request(
            [
                Message(
                    role="system",
                    content='识别本次请求的意图及操作范围，不回答或执行。生成草案不需要用户先审批或确认，不因业务资格未知而澄清；已给订单ID则order_id逐字提取，needs_clarification=false。确实缺少商品、数量、订单或范围才true，question说明缺项。memory=回忆本会话用户早前说过的约束、预算、代号、偏好、纠正或长期记忆。knowledge=查询业务文档政策规则。order_lookup=只查具体模拟订单状态。生成支付草案是pay；生成取消草案是cancel；申请整单退款草案是refund；下单草案是place_order；工单草案是ticket。取消及关联整单退款是一项取消动作，应选cancel；单独整单退款而不取消则选refund。EXAMPLE JSON: 取消及关联退款 -> {"intent":"cancel"}；生成整单退款草案 -> {"intent":"refund"}。草案不执行，等待确认，不会改变业务状态，但意图仍属于对应动作，不能选order_lookup。根据早前对话或纠正核对个人信息属于memory，不能因未看到具体值而选clarify。JSON only.',
                ),
                Message(role="user", content=current_text),
            ],
            e.run["context_window"],
            schema=review_type.model_json_schema(),
            output=512,
        )
        checked = await e.generate(review, "intent_review")
        revised = review_type.model_validate_json(checked.message.content or "")
        store.add_private(
            Event,
            e.run["id"],
            {
                "kind": "intent_reviewed",
                "original": e.analysis.intent,
                "reviewed": revised.intent,
                "source": "same_channel_real_model",
            },
        )
        e.analysis = e.analysis.model_copy(
            update={
                **revised.model_dump(),
                **(
                    {"needs_clarification": False, "question": None}
                    if revised.intent == "memory"
                    else {}
                ),
            }
        )
    store.update_run(
        e.run["id"], intent=e.analysis.intent, needs_clarification=e.analysis.needs_clarification
    )
    user = next(
        r
        for r in reversed(store.history(e.actor, e.run["conversation_id"]))
        if r["run_id"] == e.run["id"] and r["role"] == "user"
    )
    try:
        store.sync_task_state(e.actor, e.run["id"], e.analysis.task_updates, user["id"])
    except ModelError as error:
        store.add_private(
            Event,
            e.run["id"],
            {
                "kind": "task_state_proposal_rejected",
                "reason": str(error),
                "prior_state_preserved": True,
            },
        )
    effective_state = store.task_state(e.actor, e.run["conversation_id"])
    conflicts = effective_state.view()["conflicts"]
    if conflicts:
        names = {"budget_cny": "预算", "response_language": "回答语言", "tracking_code": "代号"}
        store.update_run(e.run["id"], needs_clarification=True, task_state_conflicts=conflicts)
        return {
            "finished": True,
            "result": {
                "status": "clarification",
                "answer": "同一属性存在不同要求："
                + "、".join(names.get(key, key) for key in conflicts)
                + "。请明确是否纠正为新值；本轮没有生成业务草案，原要求仍保留。",
                "task_state_conflicts": conflicts,
            },
        }
    if e.analysis.correction and re.search(
        r"^\s*(纠正|更正)\s*[:：]|(?:偏好|代号|名字|称呼|语言|回答).{0,40}(改为|改成)|之前.+(?:错|不对)",
        user.get("content") or "",
    ):
        store.invalidate_candidates(e.actor, e.run["conversation_id"])
        store.update_run(e.run["id"], correction_message_id=user["id"])
    session_only = bool(
        re.search(r"本会话|当前会话|本次任务|这次任务|本轮", user["content"])
        or any(f.message_id == user["id"] and f.replaces for f in effective_state.facts)
    ) and not re.search(r"长期记忆|跨会话", user["content"])
    candidate_count = 0
    for candidate in (
        e.analysis.candidates if e.analysis.intent == "memory" and not session_only else []
    ):
        try:
            store.candidate(
                e.actor,
                e.run["conversation_id"],
                user["id"],
                candidate,
                result.model_id,
                False,
            )
            candidate_count += 1
        except ModelError:
            store.add_private(
                Event,
                e.run["id"],
                {"kind": "memory_candidate_rejected", "reason": "source_or_safety_validation"},
            )
    if (
        session_only
        and e.analysis.intent == "memory"
        and not any(w in user["content"] for w in ("回忆", "核对", "查询", "什么", "？", "?"))
    ):
        effective = store.task_state(e.actor, e.run["conversation_id"])
        if any(f.message_id == user["id"] for f in effective.facts):
            return {
                "finished": True,
                "result": {
                    "status": "task_state_updated",
                    "answer": "已核对并保存本会话要求及来源，当前状态版本为 v"
                    + str(effective.version)
                    + "。",
                    "task_state_version": effective.version,
                    "scope": "conversation",
                },
            }
    if e.analysis.needs_clarification:
        return {
            "finished": True,
            "result": {
                "status": "clarification",
                "answer": "请补充或核对具体订单、商品和操作范围。本轮尚未核实业务事实或执行业务操作。",
            },
        }
    if (
        e.analysis.intent == "memory"
        and candidate_count
        and not any(
            word in user["content"] for word in ("？", "?", "请核对", "请基于", "回忆", "什么")
        )
    ):
        return {
            "finished": True,
            "result": {
                "status": "memory_candidate_pending",
                "answer": "已提出" + str(candidate_count) + "条记忆候选；确认后才用于长期记忆。",
            },
        }
    # Workflow-first mandatory reads are selected by the real closed LLM intent.
    # They still go through the same validation/budget/loop-controlled tool executor.
    tool = None
    if e.analysis.intent == "knowledge":
        tool = ToolCall(
            id="intent-" + uuid4().hex,
            name="search_knowledge",
            arguments={"query": user["content"]},
        )
    elif e.analysis.intent == "memory" and store.memories(e.actor):
        tool = ToolCall(
            id="intent-" + uuid4().hex, name="read_memories", arguments={"query": user["content"]}
        )
    elif e.analysis.intent in {"pay", "cancel", "refund"}:
        oid = e.analysis.order_id
        source_text = serial(store.history(e.actor, e.run["conversation_id"]))
        if not oid or oid not in source_text:
            return {
                "finished": True,
                "result": {"status": "clarification", "answer": "请提供具体订单ID和动作范围。"},
            }
        tool = ToolCall(
            id="intent-" + uuid4().hex,
            name="propose_action",
            arguments={
                "kind": {"pay": "pay", "cancel": "cancel_order", "refund": "refund"}[
                    e.analysis.intent
                ],
                "order_id": oid,
            },
        )
    elif e.analysis.intent == "order_lookup":
        oid = e.analysis.order_id
        source_text = serial(store.history(e.actor, e.run["conversation_id"]))
        if not oid or oid not in source_text:
            return {
                "finished": True,
                "result": {"status": "clarification", "answer": "请提供可核对的订单ID。"},
            }
        tool = ToolCall(id="intent-" + uuid4().hex, name="read_order", arguments={"order_id": oid})
    if tool:
        message = Message(role="assistant", tool_calls=[tool])
        store.append(e.run["id"], message.model_dump(mode="json"))
        store.add_private(
            Event,
            e.run["id"],
            {"kind": "tool_plan_validated", "source": "closed_llm_intent_route", "tool": tool.name},
        )
        return {"finished": False, "response": message}
    return {"finished": False}


async def choose(state: LoopState, e: Execution):
    e.check()
    if e.analysis.intent == "clarify":
        return {
            "finished": True,
            "result": {"status": "acknowledged", "answer": "已收到。本轮没有业务动作。"},
        }
    if e.analysis.intent == "memory":
        req = await e.service.context.assemble(
            e.actor,
            e.run,
            schema=RecallSelection.model_json_schema(),
            memories=e.memories,
            actions=e.actions,
            generate_summary=e.generate,
            factor=e.factor,
            instructions="核对current_request需要的用户事实。只输出source_ids数组，选择能支持当前事实的用户消息ID或已确认记忆ID。source_ids只使用先前用户消息的message_id/source_id或已确认记忆id，不能使用任务条目id，也不能把本次询问自己的ID当作事实来源。最新纠正优先，回忆当前值不要引用已纠正旧值，忽略助手消息和无关材料；没有来源时空数组。应用读取原文，不要生成或改写事实。",
        )
        response = await e.generate(req, "recall")
        selection = RecallSelection.model_validate_json(response.message.content or "")
        history = e.service.store.history(e.actor, e.run["conversation_id"])
        sources = {
            r["id"]: r for r in history if r["role"] == "user" and r["run_id"] != e.run["id"]
        }
        current = {m["id"]: m for m in e.service.store.memories(e.actor)}
        memory_ids = {m["id"] for m in e.memories}
        for memory in e.memories:
            authoritative = current.get(memory["id"])
            if authoritative is None or authoritative["version"] != memory["version"]:
                if memory["id"] in selection.source_ids:
                    raise ModelError("memory_changed_during_run")
                continue
            sources[memory["id"]] = {
                "content": authoritative["text"],
                "position": 10**12,
                "memory_version": authoritative["version"],
            }
        task_state = e.service.store.task_state(e.actor, e.run["conversation_id"])
        historical = bool(
            re.search(
                r"对比|原来|变更历史|之前的",
                next(
                    (
                        r["content"]
                        for r in reversed(history)
                        if r["run_id"] == e.run["id"] and r["role"] == "user"
                    ),
                    "",
                ),
            )
        ) and not re.search(
            r"当前|最新|现在",
            next(
                (
                    r["content"]
                    for r in reversed(history)
                    if r["run_id"] == e.run["id"] and r["role"] == "user"
                ),
                "",
            ),
        )
        quotes = []
        for mid in dict.fromkeys(selection.source_ids):
            if mid not in sources:
                raise ModelError("unverifiable_conversation_fact")
            source = sources[mid]
            related = [f for f in task_state.facts if f.message_id == mid]
            if related and mid not in memory_ids and not historical:
                by_id = {f.id: f for f in task_state.facts}
                for fact in related:
                    while fact.status == "superseded" and fact.superseded_by:
                        fact = by_id[fact.superseded_by]
                    if fact.status == "active":
                        value = {
                            "message_id": fact.message_id,
                            "quote": fact.quote,
                            "source_type": "effective_task_state",
                            "position": fact.position,
                        }
                        if value not in quotes:
                            quotes.append(value)
                continue
            quotes.append(
                {
                    "message_id": mid,
                    "quote": source["content"],
                    "source_type": "confirmed_memory"
                    if mid in memory_ids
                    else "conversation_history",
                    "position": source.get("position", 10**12),
                }
            )
        quotes.sort(key=lambda q: q["position"])
        for quote in quotes:
            quote.pop("position", None)
        return {
            "finished": True,
            "result": {
                "status": "recalled" if quotes else "no_memory",
                "answer": "核对的原文（按时间顺序，后续纠正优先）：\n"
                + "\n".join(q["quote"] for q in quotes)
                if quotes
                else "没有可核对的记忆来源。",
                "source_quotes": quotes,
            },
        }
    if e.tools.retrieval is not None:
        retrieved = e.tools.retrieval
        if retrieved["status"] != "grounded":
            return {
                "finished": True,
                "result": {
                    "status": retrieved["status"],
                    "answer": "资料冲突，请转人工核对。"
                    if retrieved["status"] == "source_conflict"
                    else "本轮没有足够证据，无法回答。",
                    "citations": [],
                },
            }
        req = await e.service.context.assemble(
            e.actor,
            e.run,
            schema=Selection.model_json_schema(),
            memories=e.memories,
            actions=e.actions,
            generate_summary=e.generate,
            factor=e.factor,
            instructions="回答本次current_request的政策问题。仅选择最后search_knowledge工具结果value.evidence中的证据ID和逐字quote。"
            "用户消息ID、摘要与记忆不是政策证据。decision=answer表示证据能回答，包含否定答案。"
            "例如问已发货能否取消，证据说不允许，应选answer并逐字引用否定句。"
            "answer必须有quotes；只有资料根本不涉及该问题才no_answer且quotes必须空。"
            "只选1到2个完整原文句子，优先与问题最相关的单句，不引用标题或整段，不加入换行转义字符。不要执行业务动作。",
        )
        response = await e.generate(req, "grounded_answer")
        selection = Selection.model_validate_json(response.message.content or "")
        return {"finished": True, "result": publish(selection, retrieved["evidence"])}
    if e.analysis.intent == "place_order":
        req = await e.service.context.assemble(
            e.actor,
            e.run,
            schema=OrderDraftSelection.model_json_schema(),
            memories=e.memories,
            actions=e.actions,
            generate_summary=e.generate,
            factor=e.factor,
            instructions="只提取本次current_request的下单参数，输出OrderDraftSelection JSON。order_type必填为ecommerce或food；items每项必须包含product_id和整数quantity；note只取用户本次备注。不要从旧历史复制商品，不生成业务事实或确认。商品合法性、归属和价格随后由业务服务校验。只生成待确认草案，不能以自然语言代替工具执行。",
        )
        response = await e.generate(req, "order_draft_parameters")
        selected = OrderDraftSelection.model_validate_json(response.message.content or "")
        message = Message(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="plan-" + uuid4().hex,
                    name="propose_action",
                    arguments={"kind": "place_order", **selected.model_dump(mode="json")},
                )
            ],
        )
        e.service.store.append(e.run["id"], message.model_dump(mode="json"))
        e.service.store.add_private(
            Event,
            e.run["id"],
            {
                "kind": "tool_plan_validated",
                "source": "structured_llm_order_parameters",
                "tool": "propose_action",
            },
        )
        return {"response": message, "finished": False}
    req = await e.service.context.assemble(
        e.actor,
        e.run,
        tools=e.tools.definitions(e.analysis.intent),
        schema=StepOutput.model_json_schema(),
        memories=e.memories,
        actions=e.actions,
        generate_summary=e.generate,
        factor=e.factor,
        instructions=FINAL_INSTRUCTIONS,
    )
    result = await e.generate(req, "agent")
    if not result.message.tool_calls:
        step = StepOutput.model_validate_json(result.message.content or "")
        if step.action == "tool":
            allowed = {t.name for t in e.tools.definitions(e.analysis.intent)}
            if (
                not step.calls
                or step.final is not None
                or any(c.name not in allowed for c in step.calls)
            ):
                raise ModelError("tool_plan_invalid")
            result.message = Message(
                role="assistant",
                tool_calls=[
                    ToolCall(id="plan-" + uuid4().hex, name=c.name, arguments=c.arguments)
                    for c in step.calls
                ],
            )
            e.service.store.add_private(
                Event,
                e.run["id"],
                {
                    "kind": "tool_plan_validated",
                    "source": "structured_llm_plan",
                    "count": len(step.calls),
                },
            )
        elif step.calls or step.final is None:
            raise ModelError("tool_plan_invalid")
    if result.message.tool_calls:
        # Persist an entire group as it completes; an interrupted partial group is excluded by context.
        e.service.store.append(e.run["id"], result.message.model_dump(mode="json"))
        return {"response": result.message, "finished": False}
    output = step.final
    if e.tools.structured:
        return {
            "finished": True,
            "result": {
                "status": "business_result",
                "answer": "已读取本轮业务结果。",
                "business": e.tools.structured,
            },
        }
    if e.analysis.intent == "memory" and output.source_quotes:
        sources = {r["id"]: r for r in e.service.store.history(e.actor, e.run["conversation_id"])}
        sources.update({m["id"]: {"content": m["text"]} for m in e.memories})
        valid = []
        for q in output.source_quotes:
            if q.message_id not in sources or q.quote not in (
                sources[q.message_id].get("content") or ""
            ):
                raise ModelError("unverifiable_conversation_fact")
            valid.append(q.model_dump())
        return {
            "finished": True,
            "result": {
                "status": "recalled",
                "answer": "已核对来源：\n" + "\n".join(q["quote"] for q in valid),
                "source_quotes": valid,
            },
        }
    if output.decision == "knowledge":
        retrieved = e.tools.retrieval
        if retrieved is None or retrieved["status"] != "grounded":
            result = {
                "status": "source_conflict"
                if retrieved and retrieved["status"] == "source_conflict"
                else "no_evidence",
                "answer": "资料冲突，请转人工核对。"
                if retrieved and retrieved["status"] == "source_conflict"
                else "本轮没有足够证据，无法回答。",
                "citations": [],
            }
        else:
            selection = Selection(
                decision="answer" if output.quotes else "no_answer",
                quotes=[Quote.model_validate(q) for q in output.quotes],
            )
            result = publish(selection, retrieved["evidence"])
    elif e.tools.structured:
        # Business fact text is never taken from model-generated prose.
        result = {
            "status": "business_result",
            "answer": "已读取本轮业务结果。",
            "business": e.tools.structured,
        }
    elif output.source_quotes and e.analysis.intent == "memory":
        history = {r["id"]: r for r in e.service.store.history(e.actor, e.run["conversation_id"])}
        sources = {**history, **{m["id"]: {"content": m["text"]} for m in e.memories}}
        quotes = []
        for q in output.source_quotes:
            if q.message_id not in sources or q.quote not in (
                sources[q.message_id].get("content") or ""
            ):
                raise ModelError("unverifiable_conversation_fact")
            quotes.append(q.model_dump())
        result = {
            "status": "recalled",
            "answer": "已核对来源：\n" + "\n".join(q["quote"] for q in quotes),
            "source_quotes": quotes,
        }
    elif e.analysis.intent not in {"memory", "clarify"} and output.decision != "clarify":
        # Policy/order conclusions require current evidence/authority. No free-form hallucinated facts.
        result = {"status": "no_evidence", "answer": "尚未取得可核对的证据或业务结果，请补充范围。"}
    else:
        result = {
            "status": "clarification" if output.decision == "clarify" else "acknowledged",
            "answer": "请补充或核对具体订单、商品和操作范围。本轮尚未核实业务事实或执行业务操作。"
            if output.decision == "clarify"
            else output.reply,
        }
    return {"finished": True, "result": result}


async def execute(state: LoopState, e: Execution):
    if state.response is None:
        raise ModelError("agent_transition_invalid")
    for call in state.response.tool_calls:
        e.check()
        if e.tool_calls >= 8:
            raise ModelError("tool_call_budget_exceeded")
        e.tool_calls += 1
        e.service.store.update_run(e.run["id"], tool_calls=e.tool_calls)
        try:
            value = await e.tools.execute(call)
        except BusinessError as error:
            if isinstance(error, UnknownOutcome):
                raise
            value = {"error": error.code, "can_create_ticket": True}
            e.tools.guard.after(value)
        except (ValidationError, ValueError):
            if e.repairs >= 1:
                raise ModelError("tool_argument_repair_exhausted") from None
            e.repairs += 1
            value = {
                "error": "invalid_tool_arguments",
                "repair_remaining": 0,
                "repair_instruction": "可再尝试一次，仅使用对应kind允许的字段；pay/cancel_order/refund只有kind和order_id，不传note/order_type/items；place_order使用kind/order_type/items/note；create_ticket使用kind/order_id/message；reply_ticket使用kind/ticket_id/message。不要声称草案已生成。",
            }
            e.service.store.add_private(
                Event,
                e.run["id"],
                {"kind": "tool_validation_failed", "tool": call.name, "repair": e.repairs},
            )
        e.service.store.append(
            e.run["id"],
            Message(role="tool", tool_call_id=call.id, content=serial(value)).model_dump(
                mode="json"
            ),
        )
        e.service.store.add_private(
            Event,
            e.run["id"],
            {
                "kind": "tool_finished",
                "tool": call.name,
                "number": e.tool_calls,
                "result_id": value.get("result_id"),
                "error": value.get("error"),
            },
        )
        if call.name == "propose_action" and value.get("can_create_ticket"):
            return {
                "finished": True,
                "result": {
                    "status": "business_rejected",
                    "reason": value["error"],
                    "answer": "业务服务未允许该动作："
                    + value["error"]
                    + "。本轮没有提交业务，可核对订单或提出异常工单草案。",
                },
            }
        if call.name == "read_order" and value.get("error") == "object_not_found":
            return {
                "finished": True,
                "result": {
                    "status": "business_unavailable",
                    "reason": "object_not_found",
                    "answer": "当前身份无法读取该订单，不能核实支付或退款。请核对订单编号或创建异常工单。",
                },
            }
    if e.tools.recalled_memories is not None:
        e.memories = e.tools.recalled_memories
    if e.analysis.intent == "order_lookup" and e.tools.structured:
        return {
            "finished": True,
            "result": {
                "status": "business_result",
                "answer": "已读取本轮业务结果。",
                "business": e.tools.structured,
            },
        }
    if e.tools.action:
        return {
            "finished": True,
            "result": {
                "status": "awaiting_confirmation",
                "answer": "已生成业务草案，请在动作区确认；需要审批时由运营先审批。",
                "action": e.tools.action,
            },
        }
    return {"finished": False}


class AgentService:
    def __init__(self, business, resources, store=None, gateway_factory=None):
        self.business, self.resources = business, resources
        self.store = store or AgentStore(resources.engine)
        self.context = ContextManager(self.store)
        self.memory = MemoryIndex(self.store, Vectors(Settings.load()), resources.inference)
        self.gateway_factory = gateway_factory or (
            lambda: Gateway(Settings.load(), ModelStore(resources.engine), resources.inference)
        )
        self.loop = ReActLoop(analyse, choose, execute)
        self.tasks = {}

    def actions(self, actor, cid):
        run_ids = list(dict.fromkeys(r["run_id"] for r in self.store.history(actor, cid)))
        ids = list(dict.fromkeys(self.store.run(actor, rid).get("action_id") for rid in run_ids))
        return [self.business.action(actor, aid) for aid in ids if aid]

    async def capabilities(self, channel, window):
        # Honest declared limits, fetched from local model or Pi registry, not guessed model IDs.
        if channel == "qwen":
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                r = await client.post(
                    "http://127.0.0.1:11434/api/show", json={"model": Settings.load().ollama_model}
                )
                r.raise_for_status()
                limit = r.json().get("model_info", {}).get("qwen3.context_length", 0)
        elif channel == "chatgpt":
            g = self.gateway_factory()
            try:
                catalog = await g.pi.models()
                model = next(
                    (m for m in catalog["models"] if m["id"] == Settings.load().chatgpt_model), None
                )
                limit = model["context_window"] if model else 0
            finally:
                await g.close()
        else:
            # Official DeepSeek flash docs checked 2026-10-05: native window 1M.
            limit = 1_000_000
        if window > limit:
            raise ModelError("model_window_exceeded")

    async def submit(self, actor, cid, body, manual=False):
        run, fresh = self.store.begin(
            actor,
            cid,
            body.client_request_id,
            {"text": body.text, "mode": "compress" if manual else "turn"},
        )
        if fresh:
            if not manual:
                self.store.append(
                    run["id"], Message(role="user", content=body.text).model_dump(mode="json")
                )
            task = asyncio.create_task(self.work(actor, run, manual))
            self.tasks[run["id"]] = task
            task.add_done_callback(lambda t: self.tasks.pop(run["id"], None))
        return run

    async def work(self, actor, run, manual=False):
        gateway = self.gateway_factory()
        e = Execution(
            self,
            actor,
            run,
            gateway,
            manual=manual,
            factor=self.store.calibration(actor, run["channel"]),
        )
        status, result = "failed", {"reason": "agent_run_failed"}
        try:
            async with asyncio.timeout(600):
                await self.capabilities(run["channel"], run["context_window"])
                self.store.add_private(
                    Event,
                    run["id"],
                    {"kind": "loop_started", "engine": ENGINE_ID, "max_steps": self.loop.max_steps},
                )

                def observed(transition: Transition):
                    self.store.add_private(
                        Event,
                        run["id"],
                        {
                            "kind": "loop_transition",
                            "engine": ENGINE_ID,
                            "step": transition.step,
                            "from": transition.previous.value,
                            "to": transition.following.value,
                            "reason": transition.reason,
                            "model_calls": e.model_calls,
                            "tool_calls": e.tool_calls,
                        },
                    )

                state = await self.loop.run(e, observe=observed)
                if state.result is None:
                    raise ModelError("agent_transition_invalid")
                result = state.result
                status = "awaiting_confirmation" if result.get("action") else "completed"
                if not manual:
                    self.store.append(
                        run["id"],
                        Message(role="assistant", content=serial(result)).model_dump(mode="json"),
                    )
        except asyncio.CancelledError:
            status, result = "cancelled", {"reason": "user_cancelled_or_application_stopped"}
        except TimeoutError:
            status, result = "timed_out", {"reason": "run_deadline_exceeded"}
        except UnknownOutcome as error:
            status, result = (
                "unknown_outcome",
                {"reason": "business_outcome_unknown", "domain_refs": error.references},
            )
        except WorkerError as error:
            allowed = {
                "worker_unavailable",
                "worker_protocol_invalid",
                "worker_response_id_mismatch",
                "worker_inference_failed",
                "embedding_input_capacity_exceeded",
                "reranker_input_capacity_exceeded",
                "oauth_storage_locked",
                "oauth_login_required",
                "bridge_request_failed",
                "bridge_failed",
            }
            code = str(error) if str(error) in allowed else "worker_operation_failed"
            status, result = "stopped", {"reason": code, "diagnostic": error.diagnostic}
        except ModelError as error:
            status, result = "stopped", {"reason": str(error)}
        except ValidationError as error:
            result = {
                "reason": "model_schema_invalid",
                "validation_fields": [
                    {"location": list(e["loc"]), "type": e["type"]}
                    for e in error.errors(include_input=False, include_url=False)
                ],
            }
        except Exception as error:
            import traceback
            from pathlib import Path

            frames = traceback.extract_tb(error.__traceback__)
            result = {
                "reason": "agent_run_failed",
                "exception_type": type(error).__name__,
                "trace": [
                    {"file": Path(f.filename).name, "function": f.name, "line": f.lineno}
                    for f in frames[-5:]
                ],
            }
        finally:
            try:
                await gateway.close()
            finally:
                self.store.finish(
                    run["id"],
                    status,
                    result=result,
                    model_calls=e.model_calls,
                    tool_calls=e.tool_calls,
                    parameter_repairs=e.repairs,
                    estimator_factor=e.factor,
                )

    async def cancel(self, actor, rid):
        run = self.store.run(actor, rid)
        if run["ended"]:
            return run
        task = self.tasks.get(rid)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self.store.run(actor, rid)["ended"] is None:
                self.store.finish(
                    rid, "cancelled", result={"reason": "cancelled_before_worker_started"}
                )
        else:
            self.store.finish(
                rid,
                "interrupted",
                result={"reason": "run_worker_unavailable", "automatic_replay": False},
            )
        return self.store.run(actor, rid)

    async def confirm_action(self, actor, cid, body):
        history = self.store.history(actor, cid)
        # Only link actions that this conversation proposed; human confirmation is separate from tools.
        own_runs = {r["run_id"] for r in history}
        if not any(
            self.store.run(actor, rid).get("action_id") == body.action_id for rid in own_runs
        ):
            raise ModelError("action_not_in_conversation")
        run, fresh = self.store.begin(
            actor, cid, body.client_request_id, {"mode": "human_confirmation", **body.model_dump()}
        )
        if not fresh:
            return run
        status, result = "failed", {}
        try:
            self.store.append(
                run["id"],
                Message(
                    role="user",
                    content=("用户界面确认执行：" if body.confirm else "用户界面取消草案：")
                    + body.action_id,
                ).model_dump(mode="json"),
            )
            action = self.business.confirm(
                actor,
                body.action_id,
                Confirmation(
                    client_request_id=body.client_request_id,
                    expected_version=body.expected_version,
                    confirm=body.confirm,
                ),
            )
            result = {"status": "human_confirmation", "action": action}
            status = "completed"
            self.store.append(
                run["id"], Message(role="assistant", content=serial(result)).model_dump(mode="json")
            )
        except UnknownOutcome as error:
            status, result = (
                "unknown_outcome",
                {"domain_refs": error.references, "automatic_replay": False},
            )
        except BusinessError as error:
            result = {"reason": error.code}
        finally:
            self.store.finish(run["id"], status, result=result)
        return self.store.run(actor, run["id"])

    async def close(self):
        pending = list(self.tasks.items())
        for _, task in pending:
            task.cancel()
        await asyncio.gather(*(t for _, t in pending), return_exceptions=True)
        for rid, _ in pending:
            self.store.finish(rid, "cancelled", result={"reason": "application_stopped"})
