"""P3 HTTP entrypoints for local sessions, workflows and owned run metadata."""

from typing import Annotated, Any, cast

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import ValidationError

from app.demo.identity import Identity, Sessions
from app.workflows.engine import WorkflowEngine
from app.workflows.models import REQUESTS, Closed, RunRequest, RunResponse

router = APIRouter()


class SessionRequest(Closed):
    """Only a predefined persona can be selected."""

    persona: str


def identity(
    request: Request, workflowops_session: Annotated[str | None, Cookie()] = None
) -> Identity:
    """Resolve the server-side handle; headers and bodies carry no authority."""
    return cast(Sessions, request.app.state.sessions).get(workflowops_session)


@router.post("/api/demo/session")
def session(
    body: SessionRequest,
    request: Request,
    response: Response,
    workflowops_session: Annotated[str | None, Cookie()] = None,
) -> dict[str, str]:
    """Switch context and revoke the previous local session."""
    handle = request.app.state.sessions.switch(body.persona, workflowops_session)
    response.set_cookie("workflowops_session", handle, httponly=True, samesite="strict")
    return {"persona": body.persona, "mode": "offline", "identity_mode": "local-demo"}


@router.post("/api/workflows/{workflow_id}/runs", response_model=RunResponse)
async def run(
    workflow_id: str,
    body: RunRequest,
    request: Request,
    current: Annotated[Identity, Depends(identity)],
) -> dict[str, Any]:
    """Reject unknown workflows and forbidden fields before dispatch."""
    model = REQUESTS.get(workflow_id)
    if model is None:
        raise HTTPException(404, "unknown_workflow")
    try:
        payload = model.model_validate(body.payload)
    except ValidationError:
        raise HTTPException(422, "invalid_workflow_action") from None
    return await cast(WorkflowEngine, request.app.state.engine).run(
        workflow_id,
        payload,
        current,
        body.parent_run_id,
    )


@router.get("/api/runs/{run_id}")
def read_run(
    run_id: str,
    request: Request,
    current: Annotated[Identity, Depends(identity)],
) -> dict[str, object]:
    """Expose metadata only within the allowed caller scope."""
    return cast(WorkflowEngine, request.app.state.engine).store.read(run_id, current)
