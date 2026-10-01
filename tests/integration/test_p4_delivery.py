"""Real publication and consumer rejection for corrupted deliveries/indexes."""

import hashlib
import json
from pathlib import Path

import httpx2
import pytest
from p4_helpers import ROOT, config, run, select


@pytest.mark.parametrize(
    "mutation", ["hash", "tenant", "schema", "path", "fingerprint"]
)
def test_corrupt_delivery_fails_and_old_snapshot_remains_queryable(mutation):
    settings, _ = config()
    evidence = []
    with httpx2.Client(
        base_url="http://127.0.0.1:8010", trust_env=False, timeout=60
    ) as client:
        select(client, "operator-a")
        first = run(client, "refresh_snapshot", {}, evidence)
        assert first["status"] == "completed"
        index = Path(settings["delivery_root"]) / "tenant-a/current.json"
        saved_index = index.read_bytes()
        assert run(client, "refresh_snapshot", {}, evidence)["status"] == "completed"
        publication = json.loads(index.read_bytes())
        path = index.parent / publication["export_id"]
        if mutation == "hash":
            (path / "orders_v1.jsonl").write_bytes(b"{}\n")
        elif mutation in {"path", "fingerprint"}:
            publication[
                {
                    "tenant": "tenant_id",
                    "path": "export_id",
                    "fingerprint": "delivery_fingerprint",
                }[mutation]
            ] = {"tenant": "tenant-b", "path": "../escape", "fingerprint": "0" * 64}[
                mutation
            ]
            index.write_text(json.dumps(publication))
        else:
            manifest = json.loads((path / "manifest.json").read_bytes())
            manifest["tenant_id" if mutation == "tenant" else "schema_sha256"] = (
                "tenant-b" if mutation == "tenant" else "0" * 64
            )
            content = (
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            ).encode()
            (path / "manifest.json").write_bytes(content)
            hashes = b"\n".join(
                d["content_sha256"].encode() for d in manifest["datasets"]
            )
            publication["delivery_fingerprint"] = hashlib.sha256(
                content + b"\n" + hashes
            ).hexdigest()
            index.write_text(json.dumps(publication))
        try:
            rejected = run(
                client, "analytics_report", {"report_id": "orders_total"}, evidence
            )
            assert rejected["status"] == "failed" and rejected["result"] is None
            assert str(path) not in json.dumps(rejected)
        finally:
            index.write_bytes(saved_index)
        restored = run(
            client, "analytics_report", {"report_id": "orders_total"}, evidence
        )
        assert restored["status"] == "completed" and restored["result"]["data"][
            "rows"
        ] == [[12]]
        assert (
            restored["domain_refs"]["delivery_fingerprint"]
            == first["domain_refs"]["delivery_fingerprint"]
        )
    evidence_path = ROOT / ".runtime/p4-delivery-evidence.jsonl"
    with evidence_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"mutation": mutation, "runs": evidence}) + "\n")


def test_service_credentials_are_not_interchangeable():
    settings, _ = config()
    with httpx2.Client(trust_env=False, timeout=30) as client:
        for name in settings["supportops_tokens"]:
            report = client.post(
                "http://127.0.0.1:8012/internal/demo/reports",
                json={"report_id": "orders_total"},
                headers={
                    "Authorization": "Bearer " + settings["supportops_tokens"][name]
                },
            )
            export = client.post(
                "http://127.0.0.1:8011/internal/demo/exports",
                json={},
                headers={
                    "Authorization": "Bearer " + settings["datacopilot_tokens"][name]
                },
            )
            assert report.status_code == export.status_code == 401
