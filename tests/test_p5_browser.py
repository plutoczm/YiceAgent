"""Browser fault artifacts stay separate across repeatable validation sessions."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import p5_browser


def test_two_browser_sessions_keep_independent_fault_backups(tmp_path, monkeypatch):
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    state = runtime / "demo-processes.json"
    evidence = runtime / "p5-browser"
    monkeypatch.setattr(p5_browser, "ROOT", tmp_path)
    monkeypatch.setattr(p5_browser, "STATE", state)
    monkeypatch.setattr(p5_browser, "EVIDENCE", evidence)
    evidence.mkdir()
    # The previous P5 evidence layout is preserved; it must not block a new session.
    old = evidence / "orders-backup.jsonl"
    old.write_bytes(b"HISTORICAL_BACKUP\n")
    schemas = []
    calls = []

    def service(service, arguments):
        calls.append((service, arguments))
        return SimpleNamespace(returncode=0)

    def up(port_free, services, test_schema):
        schemas.append(test_schema)
        directory = runtime / ("demo-" + test_schema)
        delivery = directory / "deliveries"
        export = delivery / "tenant-a" / ("b" * 32)
        export.mkdir(parents=True)
        (export / "orders_v1.jsonl").write_bytes((test_schema + "\n").encode())
        (export.parent / "current.json").write_text(json.dumps({"export_id": "b" * 32}))
        config = directory / "config.json"
        config.write_text(
            json.dumps({"test_schema": test_schema, "delivery_root": str(delivery)})
        )
        state.write_text(json.dumps({"config": str(config)}))

    monkeypatch.setattr(p5_browser, "service_process", service)
    monkeypatch.setattr(p5_browser, "up", up)
    monkeypatch.setattr(p5_browser, "down", lambda: state.unlink(missing_ok=True))

    def invoke(action):
        monkeypatch.setattr(sys, "argv", ["p5_browser.py", action])
        p5_browser.main()

    for _ in range(2):
        invoke("prepare")
        schema = schemas[-1]
        artifacts = evidence / schema
        target = (
            runtime
            / ("demo-" + schema)
            / "deliveries/tenant-a"
            / ("b" * 32)
            / "orders_v1.jsonl"
        )
        original = target.read_bytes()
        invoke("corrupt")
        assert target.read_bytes() == b"{}\n"
        assert (artifacts / "orders-backup.jsonl").read_bytes() == original
        invoke("repair")
        assert target.read_bytes() == original
        invoke("finish")
        assert not state.exists() and not (evidence / "state.json").exists()
        assert (artifacts / "finished.json").exists()
        assert (artifacts / "fault.json").exists()
    assert schemas[0] != schemas[1]
    assert old.read_bytes() == b"HISTORICAL_BACKUP\n"
    assert len(calls) == 4
    assert all(
        (evidence / s / "orders-backup.jsonl").read_bytes() == (s + "\n").encode()
        for s in schemas
    )


@pytest.mark.parametrize("schema", ["../escape", "public", "p3_test_wrong"])
def test_browser_evidence_rejects_unowned_schema(schema):
    with pytest.raises(ValueError, match="invalid browser evidence session"):
        p5_browser.session_evidence(schema)
