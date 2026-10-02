from collections.abc import AsyncIterator
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from backend.app.application.agent.graph import AgentGraph
from backend.app.application.agent.exceptions import AgentError
from backend.app.application.agent.models import AgentIntent, AgentRequest
from backend.app.application.business_analytics.errors import (
    BusinessAnalyticsAuditError,
    BusinessAnalyticsError,
    BusinessAnalyticsRequestError,
    BusinessDataDeliveryError,
    BusinessDataIntegrityError,
    BusinessDataTenantMismatchError,
    BusinessExecutionTimeoutError,
    BusinessSQLParseError,
    BusinessScopeViolationError,
    BusinessSQLAuthorizationError,
    BusinessSQLPolicyViolationError,
)
from backend.app.application.identity.models import AgentExecutionContext
from backend.app.presentation.api.dependencies.business_analytics import (
    AuthorizedAnalyticsCall,
    get_authorized_agent_execution_context,
    get_authorized_api_analytics_call,
)
from backend.app.presentation.api.dependencies.providers import get_agent_graph
from backend.app.presentation.api.schemas.business_analytics import (
    BusinessAnalyticsAgentRequest,
    BusinessAnalyticsQueryResponse,
    SnapshotFreshnessResponse,
)
from backend.app.presentation.api.schemas.agent import AgentChatResponse


router = APIRouter(tags=["Business Analytics"])


@router.post(
    "/api/v1/business-analytics/query",
    response_model=BusinessAnalyticsQueryResponse,
    summary="Run authenticated governed business analytics",
    responses={
        401: {"description": "Bearer authentication required or invalid"},
        403: {"description": "Authenticated principal has no matching tenant grant"},
        409: {"description": "Business data delivery failed validation"},
        422: {"description": "Invalid request or unauthorized query scope"},
        503: {"description": "Identity or analytics runtime is unavailable"},
        504: {"description": "Governed query exceeded its deadline"},
    },
)
async def query_business_analytics(
    body: BusinessAnalyticsAgentRequest,
    call: AuthorizedAnalyticsCall = Depends(get_authorized_api_analytics_call),
) -> Any:
    from backend.app.application.business_analytics.models import BusinessAnalyticsRequest

    try:
        result = await call.runtime.workflow.run(
            request=BusinessAnalyticsRequest(
                question=body.question,
                engine=body.engine,
                requested_datasets=body.requested_datasets,
            ),
            context=call.business_context,
        )
    except BusinessAnalyticsError as exc:
        return _business_error(call.business_context.request_id, exc)
    except Exception:
        return _safe_error(
            call.business_context.request_id,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "business_analytics_unavailable",
            "Business analytics is temporarily unavailable",
        )
    return BusinessAnalyticsQueryResponse(
        request_id=result.request_id,
        status=result.status.value,
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        currency=result.snapshot.currency,
        snapshot_freshness=SnapshotFreshnessResponse(
            snapshot_id=result.snapshot.snapshot_id,
            generated_at=result.snapshot.generated_at,
            consistency=result.snapshot.consistency,
            datasets=result.snapshot.datasets,
        ),
        result_classification=result.result_classification,
        query_fingerprint=result.query_fingerprint,
        contract_version=result.contract_version,
    )


@router.post(
    "/api/v1/agent/business-analytics",
    response_model=AgentChatResponse,
    summary="Run the authenticated business analytics Agent capability",
    responses={
        401: {"description": "Bearer authentication required or invalid"},
        403: {"description": "Authenticated principal has no matching tenant grant"},
        422: {"description": "Question does not route to governed business analytics"},
        503: {"description": "Identity or analytics runtime is unavailable"},
    },
)
async def agent_business_analytics(
    body: BusinessAnalyticsAgentRequest,
    execution_context: AgentExecutionContext = Depends(
        get_authorized_agent_execution_context
    ),
    agent_graph: AgentGraph = Depends(get_agent_graph),
) -> Any:
    intent = agent_graph.intent_router.classify(body.question).intent
    if intent is not AgentIntent.BUSINESS_ANALYTICS:
        return _safe_error(
            execution_context.request_id,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "business_analytics_intent_required",
            "Question must target a governed business dataset",
        )
    return await _run_agent_capability(body, execution_context, agent_graph)


@router.post(
    "/api/v1/agent/business-analytics/stream",
    summary="Stream the bounded authenticated business analytics Agent result",
    responses={
        401: {"description": "Bearer authentication required or invalid"},
        403: {"description": "Authenticated principal has no matching tenant grant"},
        422: {"description": "Question does not route to governed business analytics"},
        503: {"description": "Identity or analytics runtime is unavailable"},
    },
)
async def agent_business_analytics_stream(
    body: BusinessAnalyticsAgentRequest,
    execution_context: AgentExecutionContext = Depends(
        get_authorized_agent_execution_context
    ),
    agent_graph: AgentGraph = Depends(get_agent_graph),
) -> Any:
    intent = agent_graph.intent_router.classify(body.question).intent
    if intent is not AgentIntent.BUSINESS_ANALYTICS:
        return _safe_error(
            execution_context.request_id,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "business_analytics_intent_required",
            "Question must target a governed business dataset",
        )
    request = _agent_request(body)

    async def events() -> AsyncIterator[str]:
        try:
            async for event in agent_graph.stream(
                request,
                execution_context=execution_context,
            ):
                yield (
                    f"event: {event['event']}\n"
                    f"data: {json.dumps(event['data'], ensure_ascii=True)}\n\n"
                )
        except AgentError:
            yield _sse(
                "error",
                {
                    "code": "business_analytics_unavailable",
                    "message": "Business analytics is temporarily unavailable",
                    "retryable": True,
                },
            )
            yield _sse("done", {"status": "failed"})

    return StreamingResponse(events(), media_type="text/event-stream")


async def _run_agent_capability(
    body: BusinessAnalyticsAgentRequest,
    execution_context: AgentExecutionContext,
    agent_graph: AgentGraph,
) -> Any:
    try:
        response = await agent_graph.run(
            _agent_request(body),
            execution_context=execution_context,
        )
    except Exception:
        return _safe_error(
            execution_context.request_id,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "business_analytics_unavailable",
            "Business analytics is temporarily unavailable",
        )
    return AgentChatResponse(**response.model_dump())


def _agent_request(body: BusinessAnalyticsAgentRequest) -> AgentRequest:
    return AgentRequest(
        message=body.question,
        engine=body.engine,
        requested_datasets=body.requested_datasets,
    )


def _business_error(request_id: str, error: BusinessAnalyticsError) -> JSONResponse:
    if isinstance(error, BusinessScopeViolationError):
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(error, BusinessAnalyticsRequestError):
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif isinstance(error, (BusinessDataIntegrityError, BusinessDataTenantMismatchError)):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, BusinessExecutionTimeoutError):
        status_code = status.HTTP_504_GATEWAY_TIMEOUT
    elif isinstance(
        error,
        (
            BusinessSQLAuthorizationError,
            BusinessSQLParseError,
            BusinessSQLPolicyViolationError,
        ),
    ):
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif isinstance(error, BusinessDataDeliveryError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif isinstance(error, BusinessAnalyticsAuditError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return _safe_error(request_id, status_code, error.code, error.safe_message)


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"


def _safe_error(
    request_id: str,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {"code": code, "message": message},
            "request_id": request_id,
        },
    )
