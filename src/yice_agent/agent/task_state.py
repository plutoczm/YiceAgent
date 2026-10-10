"""Sourced conversation state; user assertions are data, never execution authority."""

import hashlib
import json
import re
from collections import Counter
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from yice_agent.contracts import Closed
from yice_agent.model_store import ModelError

Row = dict[str, Any]
Kind = Literal["goal", "constraint", "unresolved"]
CORRECTION = re.compile(r"纠正|更正|改为|改成|调整为|降至|提高到|以后|从现在")
RELEVANT = re.compile(
    r"必须|不要|不能|只允许|上限|预算|纠正|更正|记住|偏好|始终|目标|希望|想要|待处理|尚未|未解决|我的.+是|[A-Z]{2,8}-[A-Z0-9][A-Z0-9-]{1,63}"
)
SENSITIVE = re.compile(r"(?i)\bsk-[\w-]{12,}|api.?key|password|bearer|密码|密钥|凭据")
BUSINESS = re.compile(r"订单.*(已支付|已发货|配送中|到账)|支付.*(成功|失败)|审批.*(通过|结果)")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


class TaskProposal(Closed):
    kind: Kind = "constraint"
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    quote: str = Field(min_length=1, max_length=2000)
    operation: Literal["set", "resolve"] = "set"
    replaces: list[str] = Field(default_factory=list, max_length=3)


class TaskFact(Closed):
    id: str
    key: str
    kind: Kind
    value: str
    quote: str
    message_id: str
    source_start: int
    source_end: int
    clause_quote: str = ""
    position: int
    origin: Literal["rule", "model"]
    scope: Literal["conversation"] = "conversation"
    status: Literal["active", "superseded", "resolved"] = "active"
    created_version: int
    ended_version: int | None = None
    superseded_by: str | None = None
    replaces: list[str] = Field(default_factory=list)


class TaskState(Closed):
    format: Literal["task-state-v1"] = "task-state-v1"
    version: int = 0
    previous_version: int | None = None
    processed_position: int = 0
    facts: list[TaskFact] = Field(default_factory=list)

    def active(self) -> list[TaskFact]:
        return [f for f in self.facts if f.status == "active"]

    def view(self) -> Row:
        active = self.active()
        counts = Counter(f.key for f in active if f.kind == "constraint")
        return {
            "version": self.version,
            "scope": "conversation",
            "authority": "sourced_user_assertions_only",
            "facts": [f.model_dump(exclude={"clause_quote"}) for f in active],
            "conflicts": sorted(k for k, count in counts.items() if count > 1),
            "inactive_count": len(self.facts) - len(active),
        }


def slot(quote: str, surrounding: str = "") -> tuple[str, str, str] | None:
    """Only narrowly parsed fields may replace a previous value by correction."""
    if re.search(r"(?:不要|不能|不是|不必|无需)\s*(?:改为|改成|调整为)", quote):
        return None
    if re.search(r"是否|是不是|能否|多少|什么|吗\s*$", quote):
        return None
    if re.search(r"预算|上限|最多", quote) or (
        re.search(r"预算|上限|最多", surrounding)
        and re.fullmatch(r"(?:改为|改成)?\s*\d+(?:\.\d+)?\s*元", quote)
    ):
        changed = re.search(r"(?:改为|改成|调整为|降至|提高到)\s*(\d+(?:\.\d+)?)\s*元", quote)
        amounts = re.findall(r"(\d+(?:\.\d+)?)\s*元", quote)
        if changed:
            return "budget_cny", format(Decimal(changed[1]).normalize(), "f"), changed[0]
        if len(amounts) == 1:
            return "budget_cny", format(Decimal(amounts[0]).normalize(), "f"), quote
    languages = re.findall(r"中文|英文|英语", quote)
    if len(languages) == 1 and re.search(
        r"回答|回复|语言|偏好|改为|改成|使用", quote + surrounding
    ):
        return "response_language", "英文" if languages[0] == "英语" else languages[0], quote
    ids = re.findall(r"\b[A-Z]{2,8}-[A-Z0-9][A-Z0-9-]{1,63}\b", quote)
    if len(ids) == 1 and re.search(r"代号|编号", quote + surrounding):
        return "tracking_code", ids[0], quote
    return None


def source_fact(
    row: Row,
    quote: str,
    key: str,
    kind: Kind,
    value: str,
    origin: Literal["rule", "model"],
    version: int,
) -> TaskFact:
    text = row.get("content") or ""
    start = text.find(quote)
    source_clause = next(
        (m[0] for m in re.finditer(r"[^，,。；;\n！？!?]+", text) if m.start() <= start < m.end()),
        quote,
    )
    if key in {"budget_cny", "response_language", "tracking_code"} and re.search(
        r"是否|是不是|能否|多少|什么|吗\s*$", source_clause
    ):
        raise ModelError("task_state_assertion_ambiguous")
    if row["role"] != "user" or start < 0 or SENSITIVE.search(quote) or BUSINESS.search(quote):
        raise ModelError("task_state_source_invalid")
    return TaskFact(
        id=fingerprint([row["id"], key, start, quote])[:32],
        key=key,
        kind=kind,
        value=value,
        quote=quote,
        message_id=row["id"],
        source_start=start,
        source_end=start + len(quote),
        clause_quote=source_clause,
        position=row["position"],
        origin=origin,
        created_version=version,
    )


def apply_fact(
    state: TaskState, fact: TaskFact, text: str, requested: list[str] | None = None
) -> None:
    matching = [f for f in state.active() if f.key == fact.key]
    if requested and len([f for f in matching if f.id in requested]) != len(set(requested)):
        raise ModelError("task_state_replacement_invalid")
    if any(f.id == fact.id for f in state.facts):
        return
    same_source = next(
        (f for f in state.active() if f.message_id == fact.message_id and f.quote == fact.quote),
        None,
    )
    if same_source is not None:
        # Refine a rule fallback with a model's attribute label without dropping any text.
        if same_source.key.startswith("user_") and fact.origin == "model":
            same_source.key, same_source.kind, same_source.origin = fact.key, fact.kind, fact.origin
        return
    active = [f for f in state.active() if f.key == fact.key]
    if any(f.value == fact.value for f in active):
        return
    targets = [f for f in active if f.id in (requested or [])]
    if requested and len(targets) != len(set(requested)):
        raise ModelError("task_state_replacement_invalid")
    known = fact.key in {"budget_cny", "response_language", "tracking_code"}
    if CORRECTION.search(text) and (known or (targets and all(f.quote in text for f in targets))):
        replaced = targets if requested else active if known else []
        for old in replaced:
            old.status, old.ended_version, old.superseded_by = "superseded", state.version, fact.id
        fact.replaces = [f.id for f in replaced]
    elif requested:
        raise ModelError("task_state_correction_ambiguous")
    state.facts.append(fact)


def refresh(
    previous: TaskState,
    history: list[Row],
    proposals: list[TaskProposal] | None = None,
    current_id: str | None = None,
) -> TaskState:
    """Atomic pure reducer. Invalid proposed patches never mutate the prior state."""
    state = previous.model_copy(deep=True)
    state.version = previous.version + 1
    state.previous_version = previous.version or None
    for row in history:
        if row["role"] != "user" or row["position"] <= previous.processed_position:
            continue
        text = row.get("content") or ""
        for match in re.finditer(r"[^，,。；;\n！？!?]+", text):
            quote = match[0].strip()
            if (
                not quote
                or SENSITIVE.search(quote)
                or BUSINESS.search(quote)
                or re.search(r"已解决|已完成|取消这项要求|不再需要", quote)
            ):
                continue
            # Recall/search requests remain current input, not durable asserted facts.
            if re.search(r"^(?:请)?(?:回忆|核对|查询|检索|总结|说明)", quote):
                continue
            parsed = slot(quote)
            if parsed:
                key, value, quote = parsed
                fact = source_fact(row, quote, key, "constraint", value, "rule", state.version)
            elif RELEVANT.search(quote):
                kind: Kind = (
                    "unresolved"
                    if re.search(
                        r"待处理|待解决|未解决|尚未|是否|是不是|能否|多少|什么|吗\s*$", quote
                    )
                    else "goal"
                    if re.search(r"目标|希望|想要", quote)
                    else "constraint"
                )
                key = "user_" + fingerprint([kind, quote])[:24]
                fact = source_fact(row, quote, key, kind, quote, "rule", state.version)
            else:
                continue
            apply_fact(state, fact, text)
        state.processed_position = max(state.processed_position, row["position"])
    if proposals:
        current_row: Row | None = next(
            (r for r in history if r["id"] == current_id and r["role"] == "user"), None
        )
        if current_row is None:
            raise ModelError("task_state_source_invalid")
        text = current_row.get("content") or ""
        for patch in proposals:
            parsed = slot(patch.quote, text)
            key, value, quote = parsed or ("user_" + patch.key, patch.quote, patch.quote)
            fact = source_fact(current_row, quote, key, patch.kind, value, "model", state.version)
            if patch.operation == "resolve":
                if any(f.id == fact.id for f in state.facts):
                    continue
                targets = [f for f in state.active() if f.id in patch.replaces]
                if (
                    not targets
                    or len(targets) != len(set(patch.replaces))
                    or not re.search(r"已解决|已完成|不再需要|取消这项要求", patch.quote)
                    or re.search(
                        r"没有|并未|尚未|不是|未解决|未完成|是否|如果|假如|不要|不能", patch.quote
                    )
                    or not all(f.quote in patch.quote for f in targets)
                ):
                    raise ModelError("task_state_resolution_ambiguous")
                for old in targets:
                    old.status, old.ended_version = "resolved", state.version
                fact.status, fact.ended_version = "resolved", state.version
                fact.replaces = [f.id for f in targets]
                state.facts.append(fact)
            else:
                apply_fact(state, fact, text, patch.replaces)
    if len(state.active()) > 64:
        raise ModelError("task_state_capacity_exceeded")
    if state.facts == previous.facts and state.processed_position == previous.processed_position:
        return previous
    return state


def validate_state(state: TaskState, history: list[Row]) -> None:
    rows = {r["id"]: r for r in history}
    ids = {f.id for f in state.facts}
    for fact in state.facts:
        row = rows.get(fact.message_id)
        if (
            row is None
            or row["role"] != "user"
            or (row.get("content") or "")[fact.source_start : fact.source_end] != fact.quote
        ):
            raise ModelError("task_state_source_invalid")
        if fact.superseded_by and fact.superseded_by not in ids:
            raise ModelError("task_state_replacement_invalid")


def pins(state: TaskState) -> list[Row]:
    return [
        {"message_id": f.message_id, "quote": f.quote, "position": f.position}
        for f in state.active()
    ]


def summary_view(content: Row, state: TaskState) -> Row:
    """Prune obsolete source clauses; keep unrelated excerpts and stored history."""
    inactive: dict[str, list[TaskFact]] = {}
    for fact in state.facts:
        if fact.status != "active":
            inactive.setdefault(fact.message_id, []).append(fact)
    result: Row = {}
    for bucket, values in content.items():
        selected: list[Row] = []
        for value in values:
            retired = inactive.get(value["message_id"], [])
            if not retired:
                selected.append(value)
                continue
            for match in re.finditer(r"[^，,。；;\n！？!?]+", value["quote"]):
                part = match[0].strip()
                if not part or any(
                    f.quote in part or part in (f.clause_quote or f.quote) for f in retired
                ):
                    continue
                item = {"message_id": value["message_id"], "quote": part}
                if item not in selected:
                    selected.append(item)
        result[bucket] = selected
    return result
