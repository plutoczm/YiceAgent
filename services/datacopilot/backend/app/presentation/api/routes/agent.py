import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from backend.app.application.agent.graph import AgentGraph
from backend.app.application.agent.exceptions import AgentError
from backend.app.application.agent.models import AgentRequest
from backend.app.presentation.api.dependencies.providers import get_agent_graph
from backend.app.presentation.api.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    LongTermMemoryCreate,
    LongTermMemoryList,
    MemoryRuleList,
    MemoryRuleUpsert,
)
from backend.app.domain.entities.memory import LongTermMemory, MemoryRule


router = APIRouter(prefix="/api/v1/agent", tags=["Agent"])


@router.get("/tools", summary="查看智能体工具及 JSON 输入模型")
def list_agent_tools(
    agent_graph: AgentGraph = Depends(get_agent_graph),
) -> dict[str, list[dict]]:
    return {"tools": agent_graph.tool_catalog()}


@router.delete("/sessions/{session_id}", summary="清除会话记忆")
def clear_agent_session(
    session_id: str,
    user_id: str | None = Query(default=None),
    agent_graph: AgentGraph = Depends(get_agent_graph),
) -> dict[str, str | bool]:
    return {
        "session_id": session_id,
        "cleared": agent_graph.clear_memory(session_id, user_id=user_id),
    }


@router.get("/sessions/{session_id}/state", summary="读取最近一次 Agent 会话状态")
def get_agent_session_state(
    session_id: str,
    user_id: str | None = Query(default=None),
    agent_graph: AgentGraph = Depends(get_agent_graph),
) -> dict:
    return {
        "session_id": session_id,
        "state": agent_graph.load_session_state(session_id, user_id=user_id),
    }


@router.post(
    "/memory/long-term",
    response_model=LongTermMemory,
    status_code=status.HTTP_201_CREATED,
    summary="显式写入长期记忆",
)
async def add_long_term_memory(
    request: LongTermMemoryCreate,
    agent_graph: AgentGraph = Depends(get_agent_graph),
) -> LongTermMemory:
    return await agent_graph.add_long_term_memory(request.user_id, request.content)


@router.get(
    "/memory/long-term/{user_id}",
    response_model=LongTermMemoryList,
    summary="列出用户长期记忆",
)
async def list_long_term_memories(
    user_id: str,
    agent_graph: AgentGraph = Depends(get_agent_graph),
) -> LongTermMemoryList:
    return LongTermMemoryList(
        memories=await agent_graph.list_long_term_memories(user_id)
    )


@router.delete(
    "/memory/long-term/{user_id}/{memory_id}",
    summary="删除用户长期记忆",
)
def delete_long_term_memory(
    user_id: str,
    memory_id: str,
    agent_graph: AgentGraph = Depends(get_agent_graph),
) -> dict[str, str | bool]:
    return {
        "memory_id": memory_id,
        "deleted": agent_graph.delete_long_term_memory(user_id, memory_id),
    }


@router.get("/memory/rules", response_model=MemoryRuleList, summary="列出规则记忆")
def list_memory_rules(
    user_id: str | None = Query(default=None),
    agent_graph: AgentGraph = Depends(get_agent_graph),
) -> MemoryRuleList:
    return MemoryRuleList(rules=agent_graph.list_memory_rules(user_id))


@router.put(
    "/memory/rules/{rule_id}",
    response_model=MemoryRule,
    summary="新增或更新规则记忆",
)
def upsert_memory_rule(
    rule_id: str,
    request: MemoryRuleUpsert,
    agent_graph: AgentGraph = Depends(get_agent_graph),
) -> MemoryRule:
    return agent_graph.upsert_memory_rule(request.to_rule(rule_id))


@router.delete("/memory/rules/{rule_id}", summary="删除规则记忆")
def delete_memory_rule(
    rule_id: str,
    user_id: str | None = Query(default=None),
    agent_graph: AgentGraph = Depends(get_agent_graph),
) -> dict[str, str | bool]:
    return {
        "rule_id": rule_id,
        "deleted": agent_graph.delete_memory_rule(rule_id, user_id=user_id),
    }


@router.post(
    "/chat",
    response_model=AgentChatResponse,
    summary="DataPilot-AI 统一智能体对话",
    description="识别用户意图并调用对应的 DataPilot-AI 工作流。",
)
async def agent_chat(
    request: AgentChatRequest,
    agent_graph: AgentGraph = Depends(get_agent_graph),
) -> AgentChatResponse:
    response = await agent_graph.run(AgentRequest(**request.model_dump()))
    return AgentChatResponse(**response.model_dump())


@router.post(
    "/chat/stream",
    summary="DataPilot-AI 智能体流式对话",
    description="以服务器发送事件返回统一智能体工作流结果。",
)
async def agent_chat_stream(
    request: AgentChatRequest,
    agent_graph: AgentGraph = Depends(get_agent_graph),
) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in agent_graph.stream(AgentRequest(**request.model_dump())):
                yield _sse(event["event"], event["data"])
        except AgentError as exc:
            yield _sse(
                "error",
                {"code": "agent_error", "message": str(exc), "retryable": True},
            )
            yield _sse("done", {"status": "failed"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"
