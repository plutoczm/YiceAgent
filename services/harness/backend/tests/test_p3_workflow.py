"""P3 boundary, graph, transport and privacy tests."""

import ast
import asyncio
import json
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.adapters.supportops import SupportOpsAdapter
from app.demo.composition import demo_app
from app.demo.identity import PERSONAS
from app.runs.store import RunStore
from app.workflows.engine import WorkflowEngine
from app.workflows.models import Review

TOKENS = {name: name + "-" + "s" * 40 for name in PERSONAS}


def app_client(tmp_path, handler):
    adapter = SupportOpsAdapter(TOKENS, transport=httpx2.MockTransport(handler))
    return TestClient(demo_app(adapter, tmp_path))


def select(client, persona="customer-a1"):
    assert client.post("/api/demo/session", json={"persona": persona}).status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"object_id": "ORD-1001", "tenant_id": "tenant-b"},
        {"object_id": "../private"},
        {"object_id": "x?token=secret"},
        {"object_id": "x", "roles": ["support_admin"]},
    ],
)
def test_closed_inputs_never_dispatch(tmp_path, payload):
    def handler(request):
        raise AssertionError("invalid action dispatched")

    with app_client(tmp_path, handler) as client:
        select(client)
        assert (
            client.post("/api/workflows/support_lookup/runs", json={"payload": payload}).status_code
            == 422
        )
        assert (
            client.post("/api/workflows/unknown_workflow/runs", json={"payload": {}}).status_code
            == 404
        )
    assert list(tmp_path.iterdir()) == []


def test_role_denial_recorded_without_dispatch(tmp_path):
    with app_client(tmp_path, lambda _: pytest.fail("unauthorized dispatch")) as client:
        select(client)
        run = client.post(
            "/api/workflows/support_review/runs",
            json={
                "payload": {
                    "operation_id": "OP-1",
                    "decision": "approve",
                    "expected_version": 1,
                }
            },
        ).json()
        assert run["status"] == "failed" and run["error"] == "authorization_denied"
        assert [s["name"] for s in run["steps"]] == ["validate", "authorize", "format_record"]


@pytest.mark.parametrize("error", [httpx2.ReadTimeout, httpx2.ConnectError])
def test_unknown_write_has_operation_and_no_retry(tmp_path, error):
    calls = []

    def handler(request):
        calls.append(request)
        raise error("sensitive exception: token, conversation, SQL")

    with app_client(tmp_path, handler) as client:
        select(client, "operator-a")
        run = client.post(
            "/api/workflows/support_review/runs",
            json={
                "payload": {
                    "operation_id": "OP-known",
                    "decision": "approve",
                    "expected_version": 1,
                }
            },
        ).json()
        assert run["status"] == "unknown_outcome"
        assert run["domain_refs"]["operation_id"] == "OP-known"
        assert len(calls) == 1
    logs = "".join(p.read_text() for p in tmp_path.iterdir())
    assert "sensitive exception" not in logs and "s" * 40 not in logs


def test_read_timeout_is_failed(tmp_path):
    def handler(request):
        raise httpx2.ReadTimeout("private")

    with app_client(tmp_path, handler) as client:
        select(client)
        run = client.post(
            "/api/workflows/support_lookup/runs", json={"payload": {"object_id": "ORD-1001"}}
        ).json()
        assert run["status"] == "failed" and run["error"] == "downstream_timeout"


def test_run_ownership_parent_and_incomplete_tail(tmp_path):
    store = RunStore(tmp_path)
    handle = "a" * 32
    store.start(handle, PERSONAS["customer-a1"], "support_message", None)
    with store.path(handle).open("a") as stream:
        stream.write('{"partial":')
    assert store.read(handle, PERSONAS["customer-a1"])["status"] == "incomplete"
    assert store.read(handle, PERSONAS["operator-a"])["status"] == "incomplete"
    for other in ("customer-a2", "customer-b1"):
        with pytest.raises(Exception) as exception:
            store.read(handle, PERSONAS[other])
        assert exception.value.status_code == 404
    with app_client(tmp_path, lambda _: pytest.fail("parent mismatch dispatched")) as client:
        select(client, "customer-a2")
        assert (
            client.post(
                "/api/workflows/support_lookup/runs",
                json={
                    "payload": {"object_id": "ORD-1001"},
                    "parent_run_id": handle,
                },
            ).status_code
            == 404
        )


def test_session_switch_and_origin(tmp_path):
    with app_client(tmp_path, lambda _: pytest.fail("dispatch")) as client:
        assert client.get("/api/runs/" + "a" * 32).status_code == 401
        assert client.post("/api/demo/session", json={"persona": "admin"}).status_code == 422
        select(client)
        old = client.cookies.get("workflowops_session")
        select(client, "customer-a2")
        assert old not in client.app.state.sessions.handles
        assert (
            client.post(
                "/api/demo/session",
                json={"persona": "operator-a"},
                headers={"Origin": "https://external.example"},
            ).status_code
            == 403
        )


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(302, headers={"Location": "https://external.example"}),
        httpx2.Response(200, json={"unexpected": "private"}),
        httpx2.Response(403, json={"detail": "private identity"}),
        httpx2.Response(500, text="private database credentials"),
    ],
)
def test_downstream_failures_are_sanitized(tmp_path, response):
    calls = []

    def handler(request):
        calls.append(request)
        return response

    with app_client(tmp_path, handler) as client:
        select(client)
        run = client.post(
            "/api/workflows/support_lookup/runs", json={"payload": {"object_id": "ORD-1001"}}
        ).json()
        assert run["status"] == "failed" and run["result"] is None
    assert len(calls) == 1 and "private" not in json.dumps(run)


def test_logs_exclude_message_and_domain_rows(tmp_path):
    secret = "PRIVATE_BODY_SENTINEL"

    def handler(request):
        assert request.headers["Authorization"] == "Bearer " + TOKENS["customer-a1"]
        return httpx2.Response(
            200,
            json={
                "trace_id": "TRC-1",
                "intent": "knowledge",
                "answer": secret,
                "citations": [
                    {"document_id": "KB-1", "title": "Policy", "snippet": secret, "score": 1}
                ],
            },
        )

    with app_client(tmp_path, handler) as client:
        select(client)
        run = client.post(
            "/api/workflows/support_message/runs",
            json={
                "payload": {
                    "conversation_id": "private-conversation",
                    "message": secret,
                }
            },
        ).json()
        assert run["result"]["answer"] == secret
        saved = client.get("/api/runs/" + run["run_id"]).json()
        assert "result" not in saved
    contents = "".join(p.read_text() for p in tmp_path.iterdir())
    for forbidden in (secret, "private-conversation", TOKENS["customer-a1"]):
        assert forbidden not in contents
    assert [s["name"] for s in saved["steps"]] == [
        "validate",
        "authorize",
        "invoke_domain",
        "format_record",
    ]


def test_loopback_target_only():
    with pytest.raises(ValueError):
        SupportOpsAdapter(TOKENS, "http://external.example")


def test_wire_contract_matches_domain_snapshot():
    root = Path(__file__).resolve().parents[4]
    original = ast.parse((root / "services/support/app/domain.py").read_text(encoding="utf-8"))
    mirror = ast.parse(
        (root / "services/harness/backend/src/app/adapters/supportops_dto.py").read_text(
            encoding="utf-8"
        )
    )
    originals = {node.name: node for node in original.body if isinstance(node, ast.ClassDef)}
    for node in mirror.body:
        if isinstance(node, ast.ClassDef):
            source = originals[node.name]
            for model in (node, source):
                if ast.get_docstring(model):
                    model.body.pop(0)
            assert ast.dump(node) == ast.dump(source)


def test_unknown_confirm_uses_current_action_operation(tmp_path):
    store = RunStore(tmp_path)
    parent = "b" * 32
    from app.workflows.models import RunRecord, Status

    store.start(parent, PERSONAS["customer-a1"], "support_message", None)
    store.append(
        parent,
        RunRecord(
            run_id=parent,
            parent_run_id=None,
            workflow_id="support_message",
            status=Status.CONFIRMATION,
            steps=[],
            domain_refs={"operation_id": "OP-known", "pending_action_id": "ACT-known"},
        ),
    )

    def handler(request):
        raise httpx2.ReadTimeout("private")

    with app_client(tmp_path, handler) as client:
        select(client)
        for action in ("ACT-known", "ACT-other"):
            result = client.post(
                "/api/workflows/support_confirm/runs",
                json={
                    "payload": {"action_id": action, "confirm": True},
                    "parent_run_id": parent,
                },
            ).json()
            assert result["status"] == "unknown_outcome"
            assert result["domain_refs"]["operation_id"] == action


@pytest.mark.parametrize("dispatched", [False, True])
def test_overall_deadline_depends_on_write_dispatch(tmp_path, monkeypatch, dispatched):
    adapter = SupportOpsAdapter(
        TOKENS, transport=httpx2.MockTransport(lambda _: httpx2.Response(200))
    )
    engine = WorkflowEngine(adapter, RunStore(tmp_path))

    async def timeout(state, config):
        state["dispatch"].write_started = dispatched
        raise TimeoutError

    monkeypatch.setattr(engine.graph, "ainvoke", timeout)
    result = asyncio.run(
        engine.run(
            "support_review",
            Review(
                operation_id="OP-1",
                decision="approve",
                expected_version=1,
            ),
            PERSONAS["operator-a"],
            None,
        )
    )
    assert result["status"] == ("unknown_outcome" if dispatched else "failed")
    assert result["domain_refs"]["operation_id"] == "OP-1"
    asyncio.run(adapter.close())


def test_unknown_confirm_without_parent_is_queryable(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx2.ReadTimeout("injected lost response")

    with app_client(tmp_path, handler) as client:
        select(client)
        run = client.post(
            "/api/workflows/support_confirm/runs",
            json={
                "payload": {"action_id": "ACT-known", "confirm": True},
            },
        ).json()
        assert run["status"] == "unknown_outcome"
        assert run["domain_refs"]["operation_id"] == "ACT-known"
        saved = client.get("/api/runs/" + run["run_id"]).json()
        assert saved["domain_refs"]["operation_id"] == "ACT-known"
    assert len(calls) == 1


@pytest.mark.parametrize("write", [False, True])
def test_authorize_log_failure_never_claims_unknown_write(tmp_path, monkeypatch, write):
    calls = []
    with app_client(tmp_path, lambda request: calls.append(request)) as client:
        select(client)
        store = client.app.state.engine.store
        append = store.append

        def fail_authorize_once(run_id, value):
            if getattr(value, "name", None) == "authorize":
                raise OSError("injected transient record failure")
            append(run_id, value)

        monkeypatch.setattr(store, "append", fail_authorize_once)
        workflow = "support_confirm" if write else "support_lookup"
        payload = (
            {"action_id": "ACT-known", "confirm": True} if write else {"object_id": "ORD-1001"}
        )
        run = client.post(f"/api/workflows/{workflow}/runs", json={"payload": payload}).json()
        assert run["status"] == "failed" and run["error"] == "internal_error"
        assert client.get("/api/runs/" + run["run_id"]).json()["status"] == "failed"
    assert not calls


@pytest.mark.parametrize("write", [False, True])
def test_unexpected_transport_exception_distinguishes_reads_and_writes(tmp_path, write):
    calls = []

    def handler(request):
        calls.append(request)
        raise RuntimeError("injected unexpected transport failure")

    with app_client(tmp_path, handler) as client:
        select(client)
        workflow = "support_confirm" if write else "support_lookup"
        payload = (
            {"action_id": "ACT-known", "confirm": True} if write else {"object_id": "ORD-1001"}
        )
        run = client.post(f"/api/workflows/{workflow}/runs", json={"payload": payload}).json()
        assert run["status"] == ("unknown_outcome" if write else "failed")
        assert run["error"] == "internal_error" and len(calls) == 1


def test_acknowledged_write_retains_result_when_recording_fails(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(
            200,
            json={
                "trace_id": "TRC-known",
                "action_id": "ACT-known",
                "status": "executed",
                "operation_status": "submitted",
                "result": {"refund_status": "requested"},
            },
        )

    with app_client(tmp_path, handler) as client:
        select(client)
        store = client.app.state.engine.store
        append = store.append

        def fail_invocation_step(run_id, value):
            if getattr(value, "name", None) == "invoke_domain":
                raise OSError("injected record failure after acknowledgment")
            append(run_id, value)

        monkeypatch.setattr(store, "append", fail_invocation_step)
        run = client.post(
            "/api/workflows/support_confirm/runs",
            json={
                "payload": {"action_id": "ACT-known", "confirm": True},
            },
        ).json()
        assert run["status"] == "failed" and run["error"] == "internal_error"
        assert run["result"]["operation_status"] == "submitted"
        assert len(calls) == 1
