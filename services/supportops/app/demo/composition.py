"""Offline demo composition reusing the authoritative domain workflows."""

import json
import os
import re
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.auth import Principal
from app.config import Settings
from app.container import ServiceContainer
from app.demo.fixtures import validate_url
from app.demo.identity import DemoIdentity
from app.domain import SupportResponse
from app.guardrails import InputGuardrails
from app.knowledge import KnowledgeService, default_knowledge_documents
from app.main import create_app as domain_app
from app.main import get_customer_principal
from app.observability import Observability
from app.orchestrator import SupportOrchestrator
from app.policy import ActionPolicy
from app.reliability import LocalReliabilityCoordinator
from app.retrieval import BM25Retriever, RetrievalHit, RetrievalSearchResult
from app.router import IntentRouter
from app.store import SupportStore
from app.tickets import TicketWorkflow
from app.tools import SupportTools
from app.workflows import SupportWorkflows

ROOT = Path(__file__).resolve().parents[4]


class SparseEvidence:
    """BM25 only; scores are lexical evidence, never dense similarity."""

    def __init__(self, documents):
        self.sparse = BM25Retriever(documents)

    def search_with_status(self, query, *, limit=3, prefetch=10):
        matches = self.sparse.search(query, limit=limit)
        return RetrievalSearchResult(
            [
                RetrievalHit(doc, score, score, 0.0, score, score / (1 + score))
                for doc, score in matches
            ]
        )


class ExplicitAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["refund", "handoff"]
    conversation_id: str = Field(min_length=1, max_length=128)
    order_id: str | None = Field(default=None, pattern=r"^ORD-[0-9]{4,12}$")
    message: str = Field(default="Please arrange human support", min_length=1, max_length=8000)


def build_demo_container(store, tokens):
    settings = Settings(
        app_env="development",
        auth_mode="dev",
        mcp_auth_mode="disabled",
        database_auto_create_schema=False,
        metrics_enabled=False,
        tracing_enabled=False,
    )
    observation = Observability(settings)
    reliability = LocalReliabilityCoordinator()
    tickets = TicketWorkflow(store)
    workflows = SupportWorkflows(
        store=store,
        tickets=tickets,
        policy=ActionPolicy(500),
        reliability=reliability,
        action_confirmation_ttl_seconds=settings.action_confirmation_ttl_seconds,
    )
    tools = SupportTools(workflows)
    documents = default_knowledge_documents()
    knowledge = KnowledgeService(documents=documents, retriever=SparseEvidence(documents))
    orchestrator = SupportOrchestrator(
        store=store,
        guardrails=InputGuardrails(),
        router=IntentRouter(),
        knowledge=knowledge,
        tools=tools,
    )
    return ServiceContainer(
        settings,
        observation,
        DemoIdentity(tokens),
        store,
        reliability,
        tickets,
        tools,
        knowledge,
        orchestrator,
        workflows.operations,
    )


def create_demo_app(container):
    app = domain_app(container)

    @app.post("/internal/demo/actions", response_model=SupportResponse)
    def explicit_action(
        body: ExplicitAction,
        current: Annotated[Principal, Depends(get_customer_principal)],
    ):
        if body.action == "refund":
            if body.order_id is None:
                raise HTTPException(422, "order_id_required")
            # Reject hidden objects before preparing any operation or ticket.
            if (
                container.store.get_order(
                    body.order_id, current.require_customer(), tenant_id=current.tenant_id
                )
                is None
            ):
                raise HTTPException(404, "order_not_found")
            return container.orchestrator.prepare_refund(
                tenant_id=current.tenant_id,
                customer_id=current.require_customer(),
                actor_id=current.subject,
                conversation_id=body.conversation_id,
                order_id=body.order_id,
            )
        trace = "TRC-" + uuid4().hex[:16]
        safe = container.orchestrator.guardrails.inspect(body.message)
        if safe.blocked:
            raise HTTPException(422, "unsafe_message")
        ticket = container.tools.create_ticket(
            tenant_id=current.tenant_id,
            customer_id=current.require_customer(),
            actor_id=current.subject,
            conversation_id=body.conversation_id,
            reason=safe.sanitized_text,
            trace_id=trace,
        )
        return SupportResponse(
            trace_id=trace,
            intent="complaint",
            answer="Human support requested",
            ticket_id=ticket.ticket_id,
            handoff=True,
        )

    return app


def create_app():
    config_path = Path(os.environ["WORKFLOWOPS_DEMO_CONFIG"]).resolve()
    if not config_path.is_relative_to(ROOT / ".runtime"):
        raise ValueError("demo_config_outside_runtime")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["owner"] != "workflowops-demo":
        raise ValueError("unowned_demo_config")
    db = json.loads((ROOT / ".runtime/demo-db.json").read_text(encoding="utf-8"))
    validate_url(db["database_url"])
    store = SupportStore(db["database_url"])
    schema = config.get("test_schema")
    if schema is not None:
        if re.fullmatch(r"p3_test_[a-f0-9]{32}", schema) is None:
            raise ValueError("invalid_test_schema")
        store.engine.dispose()
        store.engine = create_engine(
            db["database_url"],
            connect_args={"options": f"-csearch_path={schema}"},
        )
        store._session = sessionmaker(bind=store.engine, expire_on_commit=False)
    with store.engine.connect() as connection:
        rows = connection.execute(
            text("SELECT owner, fixture_hash FROM workflowops_demo_owner")
        ).all()
        if len(rows) != 1 or rows[0][0] != "workflowops-demo" or not rows[0][1]:
            raise ValueError("demo_database_not_seeded_or_owned")
    return create_demo_app(build_demo_container(store, config["supportops_tokens"]))
