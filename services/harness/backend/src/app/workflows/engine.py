"""Bounded fixed LangGraph workflow; no autonomous planning or resume."""

import asyncio
from time import perf_counter
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.adapters.supportops import DispatchState, DomainFailure, SupportOpsAdapter
from app.demo.identity import Identity
from app.runs.store import RunStore
from app.workflows.models import (
    AnalyticsReport,
    Confirm,
    Error,
    Lookup,
    Message,
    Payload,
    RefreshSnapshot,
    Review,
    RunRecord,
    Status,
    Step,
    Ticket,
)


class State(TypedDict):
    """In-memory state only; domain payloads are never serialized to logs."""

    identity: Identity
    payload: Payload
    record: RunRecord
    result: Any
    dispatch: DispatchState


class WorkflowEngine:
    """Register finite nodes and explicit failure branches."""

    def __init__(self, adapter: SupportOpsAdapter, store: RunStore, analytics: Any = None):
        """Compile the finite graph once for this application."""
        self.adapter = adapter
        self.store = store
        self.analytics = analytics
        graph = StateGraph(State)
        graph.add_node("validate", self.validate)
        graph.add_node("authorize", self.authorize)
        graph.add_node("invoke_domain", self.invoke_domain)
        graph.add_node("format_record", self.format_record)
        graph.add_edge(START, "validate")
        graph.add_edge("validate", "authorize")
        graph.add_conditional_edges(
            "authorize",
            lambda state: "format_record" if state["record"].error else "invoke_domain",
        )
        graph.add_edge("invoke_domain", "format_record")
        graph.add_edge("format_record", END)
        self.graph = graph.compile()

    def step(self, state: State, name: Any, started: float, failed: bool = False) -> None:
        """Append a node observation with no request or result content."""
        step = Step(
            name=name,
            status="failed" if failed else "completed",
            elapsed_ms=round((perf_counter() - started) * 1000, 3),
        )
        state["record"].steps.append(step)
        self.store.append(state["record"].run_id, step)

    def validate(self, state: State) -> State:
        """Revalidate the already typed action at the graph boundary."""
        started = perf_counter()
        payload = state["payload"]
        type(payload).model_validate(payload.model_dump())
        self.step(state, "validate", started)
        return state

    def authorize(self, state: State) -> State:
        """Apply fixed workflow roles before making a domain call."""
        started = perf_counter()
        payload, identity = state["payload"], state["identity"]
        denied = (
            isinstance(payload, (RefreshSnapshot, AnalyticsReport))
            and not identity.operator
            or isinstance(payload, Review)
            and not identity.operator
            or isinstance(payload, (Confirm, Message))
            and identity.operator
            or isinstance(payload, Lookup)
            and payload.kind == "order"
            and identity.operator
            or isinstance(payload, Ticket)
            and payload.action in {"list", "assign", "transition", "operation"}
            and not identity.operator
        )
        if denied:
            state["record"].error = Error.AUTHORIZATION
            state["record"].status = Status.FAILED
        self.step(state, "authorize", started, denied)
        return state

    async def invoke_domain(self, state: State) -> State:
        """One domain request; a write failure is never retried."""
        started = perf_counter()
        try:
            adapter = (
                self.analytics
                if isinstance(state["payload"], (RefreshSnapshot, AnalyticsReport))
                else self.adapter
            )
            state["result"] = await adapter.invoke(
                state["payload"],
                state["identity"],
                dispatch=state["dispatch"],
            )
        except DomainFailure as error:
            state["record"].error = error.category
            state["record"].status = Status.UNKNOWN if error.unknown else Status.FAILED
        self.step(state, "invoke_domain", started, state["record"].error is not None)
        return state

    def format_record(self, state: State) -> State:
        """Keep only typed IDs and execution state in the durable record."""
        started = perf_counter()
        record = state["record"]
        result = state["result"]
        if result is not None:
            data = result.model_dump(mode="json") if not isinstance(result, list) else {}
            for key in ("operation_id", "pending_action_id", "ticket_id", "trace_id", "action_id"):
                value = data.get(key)
                if isinstance(value, str):
                    record.domain_refs[key] = value
            snapshot = data.get("data", {}).get("snapshot", {})
            for key in ("snapshot_id", "delivery_fingerprint"):
                value = data.get(key) or snapshot.get(key)
                if isinstance(value, str):
                    record.domain_refs[key] = value
            status = data.get("operation_status") or data.get("status")
            if status in {"pending", "pending_confirmation"} or data.get("pending_action_id"):
                record.status = Status.CONFIRMATION
            elif status == "pending_approval":
                record.status = Status.APPROVAL
        self.step(state, "format_record", started)
        self.store.append(record.run_id, record)
        return state

    async def run(
        self,
        workflow: str,
        payload: Payload,
        identity: Identity,
        parent: str | None,
    ) -> dict[str, Any]:
        """Terminate each run; subsequent human actions are new related runs."""
        refs: dict[str, str] = {}
        if parent:
            self.store.read(parent, identity)
        if isinstance(payload, Review):
            refs["operation_id"] = payload.operation_id
        if isinstance(payload, Lookup) and payload.kind == "operation":
            refs["operation_id"] = payload.object_id
        if isinstance(payload, Confirm):
            refs["action_id"] = payload.action_id
            # SupportOps v1 OperationView.operation_id is PendingActionRow.action_id.
            # Parent association is optional and never supplies this authoritative mapping.
            refs["operation_id"] = payload.action_id
        record = RunRecord(
            run_id=uuid4().hex,
            parent_run_id=parent,
            workflow_id=workflow,
            status=Status.COMPLETED,
            steps=[],
            domain_refs=refs,
        )
        self.store.start(record.run_id, identity, workflow, parent)
        dispatch = DispatchState()
        state: State = {
            "identity": identity,
            "payload": payload,
            "record": record,
            "result": None,
            "dispatch": dispatch,
        }
        try:
            final = await asyncio.wait_for(
                self.graph.ainvoke(state, config={"recursion_limit": 8}),
                timeout=60,
            )
        except TimeoutError:
            record.error = Error.TIMEOUT
            record.status = (
                Status.UNKNOWN
                if dispatch.write_started and not dispatch.outcome_known
                else Status.FAILED
            )
            self.store.append(record.run_id, record)
            final = state
        except Exception:
            # Never expose exceptions; an unexpected error after dispatch is ambiguous.
            record.error = Error.INTERNAL
            record.status = (
                Status.UNKNOWN
                if dispatch.write_started and not dispatch.outcome_known
                else Status.FAILED
            )
            self.store.append(record.run_id, record)
            final = state
        response = record.model_dump(mode="json")
        result = final["result"] if final["result"] is not None else dispatch.result
        response["result"] = (
            [item.model_dump(mode="json") for item in result]
            if isinstance(result, list)
            else result.model_dump(mode="json")
            if result is not None
            else None
        )
        return response
