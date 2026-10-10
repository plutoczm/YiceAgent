"""Closed Agent tools reuse business authorization; only action drafts can write."""

import json
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from yice_agent.agent.store import Result, digest
from yice_agent.business.contracts import DRAFT, CartItem, ObjectId
from yice_agent.contracts import Closed, ToolDefinition
from yice_agent.model_store import ModelError
from yice_agent.tokens import count_text


class Search(Closed):
    query: str = Field(min_length=1, max_length=2000)


class OrderRead(Closed):
    order_id: ObjectId


class Empty(Closed):
    pass


class Products(Closed):
    offset: int = Field(default=0, ge=0, le=100)


class ResultRead(Closed):
    result_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    offset: int = Field(default=0, ge=0, le=10000)


class DraftArgs(Closed):
    kind: str = Field(pattern=r"^(place_order|pay|cancel_order|refund|create_ticket|reply_ticket)$")
    order_id: ObjectId | None = None
    order_type: str | None = Field(default=None, pattern=r"^(ecommerce|food)$")
    items: list[CartItem] | None = Field(default=None, max_length=10)
    ticket_id: ObjectId | None = None
    message: str | None = Field(default=None, min_length=1, max_length=2000)
    note: str | None = Field(default=None, max_length=200)


ARGUMENTS = {
    "search_knowledge": Search,
    "read_memories": Search,
    "list_products": Products,
    "read_order": OrderRead,
    "list_tickets": Empty,
    "propose_action": DraftArgs,
    "read_result": ResultRead,
}
DESCRIPTIONS = {
    "search_knowledge": "检索演示政策，返回本轮可引用证据和冲突状态。",
    "read_memories": "检索当前用户已确认长期记忆，订单状态不属于长期记忆。",
    "list_products": "按页查商品，每页5项，价格由业务服务提供。",
    "read_order": "读取当前客户有权访问的真实模拟订单及版本。",
    "list_tickets": "读取当前客户的工单。",
    "propose_action": "仅生成待确认草案，不执行业务。pay/cancel_order/refund只能传kind和order_id，不要传note、order_type、items；place_order可传kind/order_type/items/note；create_ticket可传kind/order_id/message；reply_ticket可传kind/ticket_id/message。缺少对象先澄清。",
    "read_result": "读取本轮此前工具结果的下一页，每页5项。只接受应用返回的result_id。",
}


class LoopGuard:
    def __init__(self):
        self.signatures = []
        self.results = set()
        self.no_new = 0

    def before(self, name, args, version):
        signature = digest([name, args, version])
        if self.signatures and self.signatures[-1] == signature:
            raise ModelError("repeated_tool_call")
        sequence = self.signatures + [signature]
        for width in range(2, len(sequence) // 2 + 1):
            if sequence[-width:] == sequence[-2 * width : -width]:
                raise ModelError("tool_call_cycle")
        self.signatures.append(signature)

    def after(self, value):
        fingerprint = digest(value)
        if fingerprint in self.results:
            self.no_new += 1
        else:
            self.no_new = 0
        self.results.add(fingerprint)
        if self.no_new >= 3:
            raise ModelError("tool_no_new_information")


class Tools:
    def __init__(self, service, actor, run, guard):
        self.service, self.actor, self.run, self.guard = service, actor, run, guard
        self.retrieval = None
        self.recalled_memories = None
        self.action = None
        self.structured = []
        self.allowed_results = {}

    def definitions(self, intent=None):
        selected = {
            "knowledge": {"search_knowledge", "read_memories", "read_result"},
            "order_lookup": {"read_order", "read_result"},
            "place_order": {"list_products", "propose_action", "read_memories", "read_result"},
            "pay": {"read_order", "propose_action", "search_knowledge", "read_result"},
            "cancel": {"read_order", "propose_action", "search_knowledge", "read_result"},
            "refund": {"read_order", "propose_action", "search_knowledge", "read_result"},
            "ticket": {
                "read_order",
                "list_tickets",
                "propose_action",
                "search_knowledge",
                "read_result",
            },
            "memory": {"read_memories"},
            "clarify": set(),
        }.get(intent, set(ARGUMENTS))
        return [
            ToolDefinition(
                name=name, description=DESCRIPTIONS[name], parameters=dto.model_json_schema()
            )
            for name, dto in ARGUMENTS.items()
            if name in selected
        ]

    def version(self, name, args):
        b, a = self.service.business, self.actor
        if name in {"read_order", "propose_action"} and args.get("order_id"):
            return b.order(a, args["order_id"])["version"]
        if name == "read_memories":
            return [(m["id"], m["version"]) for m in self.service.store.memories(a)]
        if name == "list_products":
            return digest(b.products(a))
        if name == "search_knowledge":
            return digest(self.service.resources.retrieval.store.status(a))
        if name == "list_tickets":
            return digest(b.tickets(a))
        return 0

    async def execute(self, call):
        if call.name not in ARGUMENTS:
            raise ModelError("tool_not_allowed")
        dto = ARGUMENTS[call.name].model_validate(call.arguments)
        args = dto.model_dump(exclude_none=True)
        try:
            version = self.version(call.name, args)
        except Exception:
            version = "unavailable"
        self.guard.before(call.name, args, version)
        b, a, name = self.service.business, self.actor, call.name
        if name == "search_knowledge":
            value = await self.service.resources.retrieval.search(a, dto.query)
            self.retrieval = value
        elif name == "read_memories":
            value = await self.service.memory.search(a, dto.query)
            self.recalled_memories = value
        elif name == "list_products":
            all_rows = b.products(a)
            value = {
                "items": all_rows[dto.offset : dto.offset + 5],
                "total": len(all_rows),
                "next_offset": dto.offset + 5 if dto.offset + 5 < len(all_rows) else None,
            }
        elif name == "read_order":
            value = b.order(a, dto.order_id)
            self.structured.append(value)
        elif name == "list_tickets":
            value = b.tickets(a)
            self.structured.extend(value)
        elif name == "read_result":
            full = self.allowed_results.get(dto.result_id)
            if full is None:
                full = self.service.store.result(a, self.run["conversation_id"], dto.result_id)
            if isinstance(full, list):
                value = {
                    "items": full[dto.offset : dto.offset + 5],
                    "next_offset": dto.offset + 5 if dto.offset + 5 < len(full) else None,
                }
            else:
                value = full
        else:
            # The model cannot choose command IDs; a stable canonical business idempotency key.
            client_id = str(uuid5(NAMESPACE_URL, self.run["id"] + ":" + digest(args)))
            payload = {k: v for k, v in args.items() if v is not None}
            # Omit irrelevant optional fields; the existing discriminated contract rejects all extras.
            permitted = {
                "place_order": {"kind", "order_type", "items", "note"},
                "pay": {"kind", "order_id"},
                "cancel_order": {"kind", "order_id"},
                "refund": {"kind", "order_id"},
                "create_ticket": {"kind", "order_id", "message"},
                "reply_ticket": {"kind", "ticket_id", "message"},
            }[dto.kind]
            if any(k not in permitted for k in payload):
                raise ValueError("invalid_tool_arguments")
            parsed = DRAFT.validate_python({**payload, "client_request_id": client_id})
            self.service.store.update_run(self.run["id"], pending_command_id=client_id)
            value = b.propose(a, parsed)
            self.action = value
            self.service.store.update_run(
                self.run["id"], action_id=value["action_id"], action=value
            )
        raw_id = self.service.store.add_private(
            Result, self.run["id"], {"tool": name, "value": value}
        )
        self.allowed_results[raw_id] = value
        self.guard.after(value)
        output = {"result_id": raw_id, "value": value}
        if count_text(json.dumps(output, ensure_ascii=False)) > 1400:
            # Never truncate evidence blocks, amounts, conditions, rows or source relations.
            if name == "search_knowledge":
                kept = []
                for block in value.get("evidence", []):
                    candidate = {**value, "evidence": kept + [block]}
                    if count_text(json.dumps(candidate, ensure_ascii=False)) > 1200:
                        break
                    kept.append(block)
                output["value"] = {
                    **value,
                    "evidence": kept,
                    "status": value["status"] if kept else "no_evidence",
                }
                self.retrieval = output["value"]
            elif isinstance(value, list):
                output["value"] = {"items": value[:5], "total": len(value), "next_offset": 5}
            else:
                output["value"] = {
                    "stored_result": raw_id,
                    "reason": "oversized_result",
                    "reread_available": True,
                }
        return output
