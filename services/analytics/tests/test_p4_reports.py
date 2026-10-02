"""Template, demo identity, publication and independent-consumer boundaries."""

import asyncio
import hashlib
import json
import secrets
import shutil
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from backend.app.application.business_analytics.errors import BusinessAnalyticsError
from backend.app.application.demo_reports.service import TEMPLATES
from backend.app.application.identity.models import AuthenticatedPrincipal
from backend.app.composition.demo_reports import ISSUER, PERSONAS, DemoReports, demo_app
from backend.app.infrastructure.demo_delivery.resolver import PublishedDeliveryResolver
from fastapi.testclient import TestClient
from tests.infrastructure.business_analytics.test_delivery_v2 import (
    FIXTURE,
    canonical_json,
    canonical_json_line,
    refresh_descriptor_and_snapshot,
)


class Audit:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event.model_dump(mode="json"))


def publish_fixture(root):
    tenant = root / "tenant-a"
    tenant.mkdir(parents=True, exist_ok=True)
    export_id = uuid4().hex
    target = tenant / export_id
    shutil.copytree(FIXTURE, target)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["tenant_id"] = "tenant-a"
    manifest_path.write_bytes(canonical_json(manifest))
    for descriptor in manifest["datasets"]:
        filename = target / descriptor["file_name"]
        rows = [json.loads(line) for line in filename.read_bytes().splitlines()]
        for row in rows:
            row["tenant_id"] = "tenant-a"
        filename.write_bytes(b"".join(canonical_json_line(row) for row in rows))
        refresh_descriptor_and_snapshot(target, descriptor["dataset_name"])
    manifest_bytes = manifest_path.read_bytes()
    hashes = b"\n".join(
        d["content_sha256"].encode() for d in json.loads(manifest_bytes)["datasets"]
    )
    (tenant / "current.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "tenant_id": "tenant-a",
                "export_id": export_id,
                "delivery_fingerprint": hashlib.sha256(
                    manifest_bytes + b"\n" + hashes
                ).hexdigest(),
            }
        )
    )
    return target


def principal():
    return AuthenticatedPrincipal(
        issuer=ISSUER,
        subject="operator-a",
        audience=("datacopilot-demo",),
        authenticated_at=datetime.now(UTC),
    )


def test_five_templates_execute_real_duckdb_and_audit(tmp_path):
    target = publish_fixture(tmp_path)
    audit = Audit()
    service = DemoReports(tmp_path, audit)
    # Oracle derives from input records and business semantics, never report SQL.
    orders = [
        json.loads(row)
        for row in (target / "orders_v1.jsonl").read_bytes().splitlines()
    ]
    actions = [
        json.loads(row)
        for row in (target / "actions_v1.jsonl").read_bytes().splitlines()
    ]
    tickets = [
        json.loads(row)
        for row in (target / "tickets_v1.jsonl").read_bytes().splitlines()
    ]
    from collections import Counter

    now = datetime(2030, 1, 1, tzinfo=UTC)
    expected = {
        "orders_total": ((len(orders),),),
        "refund_requests_by_status": tuple(
            sorted(
                Counter(
                    a["action_status"] for a in actions if a["action_kind"] == "refund"
                ).items()
            )
        ),
        "pending_approval_count": (
            (sum(a["action_status"] == "pending_approval" for a in actions),),
        ),
        "tickets_by_status": tuple(
            sorted(Counter(t["status"] for t in tickets).items())
        ),
        "overdue_open_tickets": (
            (
                sum(
                    t["status"] not in {"resolved", "closed"}
                    and datetime.fromisoformat(t["sla_due_at"].replace("Z", "+00:00"))
                    < now
                    for t in tickets
                ),
            ),
        ),
    }
    for report_id in TEMPLATES:
        report = asyncio.run(service.run(report_id, principal(), clock=lambda: now))
        assert report.data.rows == expected[report_id]
        assert report.source == "template" and report.template_version == "v1"
        assert report.data.execution_engine == "duckdb"
        assert (
            report.data.source_plan_reauthorized
            and report.data.translated_sql_reauthorized
        )
        assert str(tmp_path) not in report.model_dump_json()
    serialized = json.dumps(audit.events)
    assert (
        len(audit.events) == 10
        and "SELECT" not in serialized
        and '"rows"' not in serialized
    )


@pytest.mark.parametrize(
    "mutation", ["hash", "tenant", "schema", "traversal", "bad_fingerprint"]
)
def test_bad_publication_rejected_without_destroying_old(tmp_path, mutation):
    old = publish_fixture(tmp_path)
    resolver = PublishedDeliveryResolver(tmp_path)
    pinned = resolver.pin("tenant-a")
    index = tmp_path / "tenant-a/current.json"
    old_index = index.read_bytes()
    new = publish_fixture(tmp_path)
    if mutation == "hash":
        (new / "orders_v1.jsonl").write_bytes(b"{}\n")
    elif mutation in {"tenant", "traversal", "bad_fingerprint"}:
        value = json.loads(index.read_bytes())
        value[
            {
                "tenant": "tenant_id",
                "traversal": "export_id",
                "bad_fingerprint": "delivery_fingerprint",
            }[mutation]
        ] = {
            "tenant": "tenant-b",
            "traversal": "../escape",
            "bad_fingerprint": "0" * 64,
        }[mutation]
        index.write_text(json.dumps(value))
    else:
        manifest = json.loads((new / "manifest.json").read_bytes())
        manifest["schema_sha256"] = "0" * 64
        (new / "manifest.json").write_bytes(canonical_json(manifest))
    audit = Audit()
    with pytest.raises(BusinessAnalyticsError):
        asyncio.run(DemoReports(tmp_path, audit).run("orders_total", principal()))
    assert audit.events[-1]["status"] == "failed"
    assert pinned.resolve("tenant-a") == old
    index.write_bytes(old_index)
    result = asyncio.run(DemoReports(tmp_path, audit).run("orders_total", principal()))
    assert result.data.rows == ((1,),)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders_v1",
        "SELECT * FROM orders_v1",
        "SELECT amount FROM forbidden",
        "SELECT reason FROM actions_v1",
        "SELECT * FROM read_csv('secret.csv')",
        "SELECT COUNT(*) FROM orders_v1; SELECT 1",
    ],
)
def test_template_candidate_is_untrusted(tmp_path, monkeypatch, sql):
    publish_fixture(tmp_path)
    monkeypatch.setitem(TEMPLATES, "orders_total", sql)
    audit = Audit()
    with pytest.raises(BusinessAnalyticsError):
        asyncio.run(DemoReports(tmp_path, audit).run("orders_total", principal()))
    assert audit.events[-1]["status"] == "failed"


def test_api_closed_inputs_credentials_and_identity(tmp_path):
    publish_fixture(tmp_path)
    tokens = {p: secrets.token_urlsafe(32) for p in PERSONAS}
    with TestClient(demo_app(tokens, tmp_path, Audit())) as client:
        for header in (
            {},
            {"Authorization": "Bearer wrong"},
            {"X-Roles": "operator-a"},
        ):
            assert (
                client.post(
                    "/internal/demo/reports",
                    json={"report_id": "orders_total"},
                    headers=header,
                ).status_code
                == 401
            )
        for name in PERSONAS - {"operator-a"}:
            assert (
                client.post(
                    "/internal/demo/reports",
                    json={"report_id": "orders_total"},
                    headers={
                        "Authorization": "Bearer " + tokens[name],
                        "X-Tenant": "tenant-a",
                        "X-Roles": "operator-a",
                    },
                ).status_code
                == 403
            )
        headers = {"Authorization": "Bearer " + tokens["operator-a"]}
        for body in (
            {"report_id": "unknown"},
            *(
                {"report_id": "orders_total", field: "untrusted"}
                for field in ("tenant_id", "sql", "path", "as_of", "policy", "question")
            ),
        ):
            assert (
                client.post(
                    "/internal/demo/reports", json=body, headers=headers
                ).status_code
                == 422
            )
        assert (
            client.post(
                "/internal/demo/reports",
                json={"report_id": "orders_total"},
                headers=headers,
            ).status_code
            == 200
        )


def test_index_pin_and_link_refusal(tmp_path):
    first = publish_fixture(tmp_path)
    resolver = PublishedDeliveryResolver(tmp_path)
    pin = resolver.pin("tenant-a")
    second = publish_fixture(tmp_path)
    assert pin.resolve("tenant-a") == first
    assert resolver.pin("tenant-a").resolve("tenant-a") == second
    with pytest.raises(BusinessAnalyticsError):
        resolver.pin("tenant-b")
    # A Windows junction needs no symlink privilege.
    import subprocess

    link = tmp_path / "linked"
    if __import__("os").name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(tmp_path / "tenant-a")],
            check=True,
            capture_output=True,
        )
        try:
            with pytest.raises(BusinessAnalyticsError):
                PublishedDeliveryResolver(link)
        finally:
            link.rmdir()
