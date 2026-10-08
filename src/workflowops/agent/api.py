"""Local authenticated private conversation/debug API and explicit memory confirmation."""

import asyncio
import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from workflowops.agent.contracts import (
    ActionConfirmation,
    ConversationConfig,
    ConversationCreate,
    MemoryConfirm,
    MemoryDelete,
    MemoryEdit,
    TurnRequest,
)
from workflowops.agent.store import Event, Result, Snapshot
from workflowops.business.api import COOKIE
from workflowops.identity import Principal


def router(service, sessions):
    api = APIRouter(prefix="/api/v2")

    def current(request: Request):
        try:
            session = sessions.get(request.cookies.get(COOKIE))
        except PermissionError:
            raise HTTPException(401, "demo_session_required") from None
        if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
            request.headers.get("X-WorkflowOps-CSRF", ""), session.csrf
        ):
            raise HTTPException(403, "csrf_required")
        return session.principal

    Actor = Annotated[Principal, Depends(current)]

    @api.post("/conversations")
    async def create(body: ConversationCreate, actor: Actor):
        await service.capabilities(body.channel, body.context_window)
        return service.store.create(actor, body)

    @api.get("/conversations")
    def conversations(actor: Actor):
        return service.store.conversations(actor)

    @api.get("/conversations/{cid}")
    def conversation(cid: str, actor: Actor):
        return {
            "conversation": service.store.conversation(actor, cid),
            "messages": service.store.history(actor, cid),
            "summary": service.store.summary(actor, cid),
            "action_states": service.actions(actor, cid),
        }

    @api.post("/conversations/{cid}/config")
    async def configure(cid: str, body: ConversationConfig, actor: Actor):
        if service.store.conversation(actor, cid)["active_run"]:
            raise HTTPException(409, "conversation_busy")
        await service.capabilities(body.channel, body.context_window)
        return service.store.configure(actor, cid, body)

    @api.post("/conversations/{cid}/turns", status_code=202)
    async def submit(cid: str, body: TurnRequest, actor: Actor):
        return await service.submit(actor, cid, body)

    @api.post("/conversations/{cid}/compress", status_code=202)
    async def compress(cid: str, body: TurnRequest, actor: Actor):
        return await service.submit(actor, cid, body, manual=True)

    @api.post("/conversations/{cid}/confirm")
    async def confirm(cid: str, body: ActionConfirmation, actor: Actor):
        return await service.confirm_action(actor, cid, body)

    @api.get("/runs/{rid}")
    def run(rid: str, actor: Actor):
        return service.store.run(actor, rid)

    @api.post("/runs/{rid}/cancel")
    async def cancel(rid: str, actor: Actor):
        return await service.cancel(actor, rid)

    @api.get("/runs/{rid}/context")
    def context(rid: str, actor: Actor):
        return service.store.private(actor, rid, Snapshot)

    @api.get("/runs/{rid}/results")
    def results(rid: str, actor: Actor):
        return service.store.private(actor, rid, Result)

    @api.get("/runs/{rid}/events")
    def events(rid: str, actor: Actor):
        return service.store.private(actor, rid, Event)

    @api.get("/runs/{rid}/stream")
    async def stream(rid: str, actor: Actor, request: Request):
        service.store.run(actor, rid)

        async def emit():
            offset = 0
            while True:
                if await request.is_disconnected():
                    break
                rows = service.store.private(actor, rid, Event)
                for item in rows[offset:]:
                    yield "event: step\ndata: " + json.dumps(item, ensure_ascii=False) + "\n\n"
                offset = len(rows)
                record = service.store.run(actor, rid)
                if record["ended"]:
                    yield "event: final\ndata: " + json.dumps(record, ensure_ascii=False) + "\n\n"
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(emit(), media_type="text/event-stream")

    @api.get("/memories")
    def memories(actor: Actor):
        return {
            "records": service.store.memories(actor),
            "candidates": service.store.memories(actor, True),
        }

    @api.post("/memories/candidates/{mid}/confirm")
    async def memory_confirm(mid: str, body: MemoryConfirm, actor: Actor):
        row = service.store.memory_command(actor, mid, body, "confirm")
        if body.confirm:
            row["index"] = await service.memory.update(actor, row)
        return row

    @api.post("/memories/{mid}/edit")
    async def memory_edit(mid: str, body: MemoryEdit, actor: Actor):
        row = service.store.memory_command(actor, mid, body, "edit")
        return {**row, "index": await service.memory.update(actor, row)}

    @api.post("/memories/{mid}/delete")
    async def memory_delete(mid: str, body: MemoryDelete, actor: Actor):
        row = service.store.memory_command(actor, mid, body, "delete")
        return {**row, "index": await service.memory.update(actor, row)}

    return api
