"""Workbench ownership, reload recovery and restricted corpus import, no provider calls."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from workflowops.app import create_app
from workflowops.config import Settings
from workflowops.identity import PERSONAS
from workflowops.models.api import Services

pytestmark = pytest.mark.integration


def session(client, name):
    r = client.post("/api/v2/demo/session", json={"persona": name})
    client.headers["X-WorkflowOps-CSRF"] = r.json()["csrf_token"]


def test_run_list_filters_subject_and_recovers_latest_manual_run(business):
    from workflowops.agent.store import AgentStore, Base
    from workflowops.database import make_engine

    domain, schema = business
    raw = make_engine(Settings.load(), schema)
    engine = raw.execution_options(schema_translate_map={"workflowops_agent": schema})
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
    agent_store = AgentStore(engine)
    from workflowops.agent.contracts import ConversationCreate

    s = agent_store
    a = PERSONAS["customer-a1"]
    c = s.create(a, ConversationCreate())
    r, _ = s.begin(a, c["id"], str(uuid4()), {"manual": True})
    s.finish(r["id"], "completed")
    assert s.runs(a, c["id"])[0]["id"] == r["id"]
    for actor in (PERSONAS["customer-a2"], PERSONAS["operator-a"], PERSONAS["customer-b1"]):
        assert s.runs(actor) == []
        with pytest.raises(Exception, match="conversation_not_found"):
            s.runs(actor, c["id"])


def test_workbench_routes_and_import_acl(business, monkeypatch):
    domain, _ = business
    resources = Services.create(domain.engine, Settings.load())
    calls = []

    async def imported(name):
        calls.append(name)
        return {"status": "no_op", "index_id": "existing"}

    monkeypatch.setattr(resources.retrieval, "import_corpus", imported)
    with TestClient(create_app(domain, resources)) as client:
        assert client.get("/").text == client.get("/agent").text
        for panel in ("chat", "business", "context", "memory", "runs"):
            assert 'data-panel="' + panel + '"' in client.get("/").text
        session(client, "customer-a1")
        assert (
            client.post("/api/v2/knowledge/import", json={"corpus": "demo-cn"}).status_code == 403
        )
        session(client, "operator-a")
        for body in ({"corpus": "file:///private"}, {"corpus": "demo-cn", "tenant": "tenant-b"}):
            assert client.post("/api/v2/knowledge/import", json=body).status_code == 422
        assert (
            client.post("/api/v2/knowledge/import", json={"corpus": "demo-cn"}).json()["status"]
            == "no_op"
        )
        client.headers.pop("X-WorkflowOps-CSRF")
        assert (
            client.post("/api/v2/knowledge/import", json={"corpus": "demo-cn"}).status_code == 403
        )
    assert calls == ["demo-cn"]
