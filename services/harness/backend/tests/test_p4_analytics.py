"""P4 dispatch authorization, credentials, safe errors and wire contract parity."""

import ast
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.adapters.analytics import AnalyticsAdapter
from app.adapters.supportops import SupportOpsAdapter
from app.demo.composition import demo_app
from app.demo.identity import PERSONAS

SUPPORT = {name: "s" * 40 + name for name in PERSONAS}
REPORTS = {name: "r" * 40 + name for name in PERSONAS}


def app_client(tmp_path, handler):
    transport = httpx2.MockTransport(handler)
    return TestClient(
        demo_app(
            SupportOpsAdapter(SUPPORT, transport=transport),
            tmp_path,
            AnalyticsAdapter(SUPPORT, REPORTS, transport=transport),
        )
    )


@pytest.mark.parametrize(
    "workflow,payload",
    [("refresh_snapshot", {}), ("analytics_report", {"report_id": "orders_total"})],
)
def test_role_and_closed_inputs(tmp_path, workflow, payload):
    with app_client(tmp_path, lambda _: pytest.fail("unauthorized dispatch")) as client:
        client.post("/api/demo/session", json={"persona": "customer-a1"})
        run = client.post(f"/api/workflows/{workflow}/runs", json={"payload": payload}).json()
        assert run["status"] == "failed" and run["error"] == "authorization_denied"
        client.post("/api/demo/session", json={"persona": "operator-a"})
        for field in ("path", "tenant_id", "sql", "as_of", "policy"):
            assert (
                client.post(
                    f"/api/workflows/{workflow}/runs",
                    json={"payload": {**payload, field: "untrusted"}},
                ).status_code
                == 422
            )


@pytest.mark.parametrize(
    "workflow,payload,port,token",
    [
        ("refresh_snapshot", {}, 8011, SUPPORT["operator-a"]),
        ("analytics_report", {"report_id": "orders_total"}, 8012, REPORTS["operator-a"]),
    ],
)
def test_p4_timeout_is_failed_once_with_service_token(tmp_path, workflow, payload, port, token):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.port == port and request.headers["authorization"] == "Bearer " + token
        raise httpx2.ReadTimeout("private SQL/token/path")

    with app_client(tmp_path, handler) as client:
        client.post("/api/demo/session", json={"persona": "operator-a"})
        response = client.post(f"/api/workflows/{workflow}/runs", json={"payload": payload})
        assert response.json()["status"] == "failed"
        assert response.json()["error"] == "downstream_timeout" and len(calls) == 1
        assert "private" not in response.text


def test_publication_refs_persist_without_rows(tmp_path):
    data = {
        "snapshot_id": "a" * 64,
        "delivery_fingerprint": "b" * 64,
        "generated_at": "2026-10-01T00:00:00Z",
        "dataset_row_counts": {"orders_v1": 12},
        "source": "supportops_delivery_v2",
    }
    with app_client(tmp_path, lambda _: httpx2.Response(200, json=data)) as client:
        client.post("/api/demo/session", json={"persona": "operator-a"})
        run = client.post("/api/workflows/refresh_snapshot/runs", json={"payload": {}}).json()
        assert (
            run["status"] == "completed"
            and run["domain_refs"]["snapshot_id"] == data["snapshot_id"]
        )
        text = (tmp_path / (run["run_id"] + ".jsonl")).read_text()
        assert "dataset_row_counts" not in text and "Bearer" not in text


def test_wire_fields_match_source():
    root = Path(__file__).resolve().parents[4]
    wire = ast.parse(
        (root / "services/harness/backend/src/app/adapters/analytics_dto.py").read_text()
    )
    wire_classes = {n.name: n for n in wire.body if isinstance(n, ast.ClassDef)}
    for source in (
        "services/support/app/demo/exports.py",
        "services/analytics/backend/app/application/demo_reports/service.py",
        "services/analytics/backend/app/application/business_analytics/governed_models.py",
        "services/analytics/backend/app/application/business_analytics/managed_models.py",
    ):
        for node in ast.parse((root / source).read_text()).body:
            if isinstance(node, ast.ClassDef) and node.name in wire_classes:

                def fields(n):
                    return {
                        item.target.id: ast.dump(item.annotation)
                        for item in n.body
                        if isinstance(item, ast.AnnAssign)
                    }

                assert fields(node) == fields(wire_classes[node.name])
