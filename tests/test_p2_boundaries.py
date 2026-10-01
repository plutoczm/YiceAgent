import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("demo_db", ROOT / "scripts/demo_db.py")
demo_db = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo_db)


@pytest.mark.parametrize("kind", ["container", "volume"])
def test_unknown_resource_refused(monkeypatch, kind):
    class Result:
        stdout = "id\n" if kind == "container" else demo_db.VOLUME + "\n"

    def run(*args, **kwargs):
        result = Result()
        if "inspect" in args:
            result.stdout = json.dumps([{"Config": {"Labels": {}}, "Labels": {}}])
        return result

    monkeypatch.setattr(demo_db, "docker", run)
    with pytest.raises(ValueError, match="unowned"):
        demo_db.inspect_resource(
            kind, demo_db.CONTAINER if kind == "container" else demo_db.VOLUME
        )


def test_unknown_resources_never_recreate_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(demo_db, "ROOT", tmp_path)
    monkeypatch.setattr(demo_db, "inspect_resource", lambda *args: {"existing": True})
    with pytest.raises(ValueError, match="without owned runtime"):
        demo_db.ensure_database(lambda port: True)
    assert not (tmp_path / ".runtime").exists()


def test_occupied_port_never_creates_resources(monkeypatch, tmp_path):
    monkeypatch.setattr(demo_db, "ROOT", tmp_path)
    monkeypatch.setattr(demo_db, "inspect_resource", lambda *args: None)
    with pytest.raises(ValueError, match="occupied"):
        demo_db.ensure_database(lambda port: False)
    assert not (tmp_path / ".runtime").exists()


def test_compose_project_override_cannot_change_startup_target(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "unrelated-project")
    monkeypatch.setattr(demo_db, "ROOT", tmp_path)
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    config = {
        "owner": demo_db.OWNER,
        "password": "test-only",
        "database_url": "postgresql+psycopg://workflowops_demo:test-only"
        "@127.0.0.1:55432/workflowops_demo",
    }
    (runtime / "demo-db.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(
        demo_db,
        "inspect_resource",
        lambda *args: {"State": {"Health": {"Status": "healthy"}}},
    )
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(demo_db, "docker", run)
    demo_db.ensure_database(lambda port: True)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:3] == ("compose", "--project-name", "workflowops-demo")
    assert args[5:] == ("up", "-d", "postgres")
    assert kwargs["env"]["COMPOSE_PROJECT_NAME"] == "unrelated-project"
