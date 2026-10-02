"""Start preflight distinguishes owned PostgreSQL from port conflicts."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import demo_db
import dev


@pytest.mark.parametrize(
    "conflict,owned,passed",
    [(None, True, True), (8011, True, False), (None, False, False)],
)
def test_doctor_occupied_port_ownership(tmp_path, monkeypatch, conflict, owned, passed):
    (tmp_path / ".runtime").mkdir()
    (tmp_path / ".runtime/demo-db.json").write_text("{}")
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "port_free", lambda port: port not in {55432, conflict})
    monkeypatch.setattr(dev, "probe_service", lambda service: {"passed": True})
    monkeypatch.setattr(dev, "docker_check", lambda: {"passed": True})

    def inspect(kind, name):
        if not owned:
            raise ValueError("unowned resource")
        return {"State": {"Health": {"Status": "healthy"}}}

    monkeypatch.setattr(demo_db, "inspect_resource", inspect)
    assert dev.doctor() == (0 if passed else 1)


def test_start_failed_doctor_never_launches(monkeypatch):
    import demo_runtime

    monkeypatch.setattr(sys, "argv", ["dev.py", "start"])
    monkeypatch.setattr(dev, "doctor", lambda: 1)
    monkeypatch.setattr(
        demo_runtime, "up", lambda *args: pytest.fail("launch after failed doctor")
    )
    assert dev.main() == 1
