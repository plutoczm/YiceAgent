"""Process lifecycle refuses foreign resources and validates all before stopping."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import demo_process
import demo_runtime
from dev import SERVICES


def test_occupied_ports_do_not_start_or_inspect(monkeypatch, tmp_path):
    monkeypatch.setattr(demo_runtime, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(
        demo_runtime, "inspect_resource", lambda *args: pytest.fail("resource touched")
    )
    with pytest.raises(ValueError, match="occupied"):
        demo_runtime.up(lambda port: False, SERVICES)
    assert not list(tmp_path.iterdir())


def test_stale_state_does_not_overwrite_config(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    state.write_text("user content")
    monkeypatch.setattr(demo_runtime, "STATE", state)
    with pytest.raises(ValueError, match="state exists"):
        demo_runtime.up(lambda port: True, SERVICES)
    assert state.read_text() == "user content"


def test_process_marker_and_executable_required():
    record = {
        "owner": "workflowops-demo",
        "marker": "ours",
        "service": "harness",
        "executable": "D:/Anaconda3/envs/agentopshub/python.exe",
    }
    demo_runtime.validate_record(record, {"marker": "ours"})
    for key, value in (
        ("owner", "foreign"),
        ("marker", "wrong"),
        ("service", "datacopilot"),
        ("executable", "C:/Windows/notepad.exe"),
    ):
        with pytest.raises(ValueError):
            demo_runtime.validate_record({**record, key: value}, {"marker": "ours"})


def test_down_prevalidates_every_pid(monkeypatch, tmp_path):
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    config = runtime / "config.json"
    config.write_text(json.dumps({"owner": "workflowops-demo", "marker": "ours"}))
    state = runtime / "state.json"
    state.write_text(json.dumps({"owner": "workflowops-demo", "config": str(config)}))
    monkeypatch.setattr(demo_runtime, "ROOT", tmp_path)
    monkeypatch.setattr(demo_runtime, "STATE", state)
    records = []
    for pid, service, environment in (
        (1, "harness", "agentopshub"),
        (2, "supportops", "supportops-ai"),
    ):
        record = {
            "owner": "workflowops-demo",
            "marker": "ours",
            "service": service,
            "config": str(config),
            "pid": pid,
            "created": 100,
            "executable": f"D:/Anaconda3/envs/{environment}/python.exe",
        }
        records.append(record)
        (runtime / (service + "-process.json")).write_text(json.dumps(record))

    def actual(pid):
        record = records[pid - 1]
        return {
            "pid": pid,
            "created": 999 if pid == 2 else 100,
            "executable": record["executable"],
        }

    monkeypatch.setattr(demo_runtime, "process_identity", actual)
    monkeypatch.setattr(
        demo_runtime, "stop_owned", lambda record: pytest.fail("partial stop")
    )
    with pytest.raises(ValueError, match="identity mismatch"):
        demo_runtime.down()
    assert state.exists()


@pytest.mark.parametrize("error", [5, 87])
def test_inaccessible_process_is_not_treated_as_stopped(monkeypatch, error):
    from types import SimpleNamespace

    def open_process(*args):
        return 0

    kernel = SimpleNamespace(OpenProcess=open_process, CloseHandle=lambda handle: None)
    monkeypatch.setattr(demo_process.ctypes, "WinDLL", lambda *args, **kwargs: kernel)
    monkeypatch.setattr(demo_process.ctypes, "get_last_error", lambda: error)
    if error == 87:
        assert demo_process.process_identity(1234) is None
    else:
        with pytest.raises(OSError, match="ownership"):
            demo_process.process_identity(1234)
