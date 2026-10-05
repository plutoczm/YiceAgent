"""Resource HTTP ACL/CSRF and secret non-reflection without real provider calls."""

import pytest
from fastapi.testclient import TestClient

from workflowops.app import create_app
from workflowops.models.api import Services

pytestmark = pytest.mark.integration


def session(client, name):
    response = client.post("/api/v2/demo/session", json={"persona": name})
    client.headers["X-WorkflowOps-CSRF"] = response.json()["csrf_token"]


def test_credential_configuration_requires_human_operator_and_hides_input(business, monkeypatch):
    service, _ = business
    import workflowops.models.api as model_api
    from workflowops.config import Settings

    resources = Services.create(service.engine, Settings.load())
    writes = []
    monkeypatch.setattr(
        model_api, "save_deepseek", lambda body: writes.append(body) or {"billing_cap_cny": "15.00"}
    )
    with TestClient(create_app(service, models=resources)) as client:
        private = {"api_key": "SYNTHETIC_LOCAL_TEST_SECRET", "enable_paid": True}
        assert client.post("/api/v2/models/deepseek/configure", json=private).status_code == 401
        session(client, "customer-a1")
        assert client.post("/api/v2/models/deepseek/configure", json=private).status_code == 403
        assert writes == []
        session(client, "operator-a")
        invalid = {**private, "tenant_id": "tenant-b"}
        reply = client.post("/api/v2/models/deepseek/configure", json=invalid)
        assert reply.status_code == 422 and "SYNTHETIC" not in reply.text
        result = client.post("/api/v2/models/deepseek/configure", json=private)
        assert result.status_code == 200 and "SYNTHETIC" not in result.text
        assert len(writes) == 1
        client.headers.pop("X-WorkflowOps-CSRF")
        assert client.post("/api/v2/models/deepseek/configure", json=private).status_code == 403


def test_knowledge_query_cannot_inject_scope_or_tool_target(business):
    service, _ = business
    from workflowops.config import Settings

    resources = Services.create(service.engine, Settings.load())
    with TestClient(create_app(service, models=resources)) as client:
        session(client, "customer-a1")
        for field, value in [
            ("tenant", "tenant-b"),
            ("index_id", "other"),
            ("url", "https://example.invalid"),
            ("tool", "shell"),
        ]:
            response = client.post(
                "/api/v2/knowledge/search", json={"query": "公开演示问题", field: value}
            )
            assert response.status_code == 422


@pytest.mark.parametrize("failure", ["missing_login", "catalog_timeout", "generation_timeout"])
def test_chatgpt_selection_failure_is_bounded_and_never_saves_config(
    business, monkeypatch, failure
):
    import asyncio
    import hashlib

    import workflowops.models.api as model_api
    import workflowops.models.gateway as gateway
    from workflowops.config import CONFIG, Settings

    service, _ = business
    resources = Services.create(service.engine, Settings.load())
    original = hashlib.sha256(CONFIG.read_bytes()).hexdigest()
    started, settled, closed = [], [], []

    class FakeLedger:
        def __init__(self, engine):
            pass

        def begin(self, *args):
            started.append(args[1])
            return "isolated-test-call"

        def finish(self, call_id, usage, settings, error):
            settled.append((call_id, usage, error))

    class FakePi:
        async def models(self, account_probe):
            if failure == "catalog_timeout":
                await asyncio.sleep(30)
            return {"logged_in": failure != "missing_login", "models": [{"id": "gpt-6.1-sol"}]}

        async def generate(self, request, model_id, scope):
            assert request.timeout_seconds == 30
            await asyncio.sleep(30)

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(gateway, "PiAdapter", FakePi)
    monkeypatch.setattr(model_api, "ModelStore", FakeLedger)
    monkeypatch.setattr(model_api, "CHATGPT_SELECTION_TIMEOUT_SECONDS", 0.02)
    with TestClient(create_app(service, models=resources)) as client:
        session(client, "operator-a")
        response = client.post("/api/v2/models/chatgpt/select", json={"model_id": "gpt-6.1-sol"})
    expected = "oauth_login_required" if failure == "missing_login" else "provider_timeout"
    assert response.status_code == (409 if failure == "missing_login" else 504)
    assert response.json() == {"error": expected}
    assert closed == [True]
    assert hashlib.sha256(CONFIG.read_bytes()).hexdigest() == original
    if failure == "generation_timeout":
        assert started == ["chatgpt"]
        assert settled == [("isolated-test-call", None, "provider_timeout")]
    else:
        assert started == settled == []


def test_cancelled_chatgpt_selection_settles_as_cancelled_and_closes_worker(business, monkeypatch):
    import asyncio
    import hashlib

    import workflowops.models.api as model_api
    import workflowops.models.gateway as gateway
    from workflowops.config import CONFIG, Settings
    from workflowops.identity import PERSONAS, Sessions

    service, _ = business
    resources = Services.create(service.engine, Settings.load())
    original = hashlib.sha256(CONFIG.read_bytes()).hexdigest()
    settled, closed = [], []

    class FakeLedger:
        def __init__(self, engine):
            pass

        def begin(self, *args):
            return "isolated-test-call"

        def finish(self, call_id, usage, settings, error):
            settled.append((call_id, usage, error))

    class FakePi:
        async def models(self, account_probe):
            return {"logged_in": True, "models": [{"id": "gpt-6.1-sol"}]}

        async def generate(self, request, model_id, scope):
            raise asyncio.CancelledError()

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(gateway, "PiAdapter", FakePi)
    monkeypatch.setattr(model_api, "ModelStore", FakeLedger)
    routes = model_api.router(resources, Sessions()).routes
    endpoint = next(r.endpoint for r in routes if r.path == "/api/v2/models/chatgpt/select")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(endpoint(model_api.ModelChoice(model_id="gpt-6.1-sol"), PERSONAS["operator-a"]))
    assert settled == [("isolated-test-call", None, "model_cancelled")]
    assert closed == [True]
    assert hashlib.sha256(CONFIG.read_bytes()).hexdigest() == original
