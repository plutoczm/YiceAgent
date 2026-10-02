"""P6 aggregate failure, skip accounting and stale-evidence protection."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import demo_process
import p6_verify


@pytest.mark.parametrize("state", ["terminated", "unknown", "active_without_image"])
def test_process_exit_state_requires_positive_os_evidence(monkeypatch, state):
    from types import SimpleNamespace

    closed = []

    def exit_code(handle, value):
        value._obj.value = 259 if state == "active_without_image" else 0
        return state != "unknown"

    kernel = SimpleNamespace(
        OpenProcess=lambda *args: 123,
        CloseHandle=lambda handle: closed.append(handle),
        GetExitCodeProcess=exit_code,
        QueryFullProcessImageNameW=lambda *args: False,
    )
    monkeypatch.setattr(demo_process.ctypes, "WinDLL", lambda *args, **kwargs: kernel)
    if state == "terminated":
        assert demo_process.process_identity(1234) is None
    else:
        with pytest.raises(
            OSError, match="exit state" if state == "unknown" else "executable"
        ):
            demo_process.process_identity(1234)
    assert closed == [123]


def test_junit_keeps_failure_and_skip(tmp_path):
    xml = tmp_path / "results.xml"
    xml.write_text(
        '<testsuites><testsuite><testcase name="pass"/><testcase name="fail"><failure/></testcase><testcase name="error"><error/></testcase><testcase name="skip"><skipped message="platform"/></testcase></testsuite></testsuites>'
    )
    assert p6_verify.junit(xml) == {
        "passed": 1,
        "failed": 2,
        "skipped": 1,
        "skip_reasons": ["platform"],
    }


@pytest.mark.parametrize("fault", ["failed", "stale", "skipped", "missing_case"])
def test_acceptance_rejects_incomplete_or_stale_evidence(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(p6_verify, "ROOT", tmp_path)
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    manifest = {
        "cases": [{"id": f"E{i:02}"} for i in range(1, 25)],
        "acceptance": {"AC-11": ["E01"]},
    }
    monkeypatch.setattr(p6_verify, "SCENARIOS", runtime / "manifest.json")
    p6_verify.save(p6_verify.SCENARIOS, manifest)
    monkeypatch.setattr(p6_verify, "LATEST", runtime / "latest.json")
    p6_verify.save(p6_verify.LATEST, {"summary": str(runtime / "summary.json")})
    monkeypatch.setattr(p6_verify, "fingerprint", lambda: "current")
    summary = {
        "passed": fault != "failed",
        "fingerprint": "old" if fault == "stale" else "current",
        "jobs": [
            {
                "name": "http-v1",
                "passed": 24,
                "failed": 0,
                "skipped": 1 if fault == "skipped" else 0,
            }
        ],
    }
    p6_verify.save(runtime / "summary.json", summary)
    cases = [{**c, "run_ids": []} for c in manifest["cases"]]
    p6_verify.save(
        runtime / "e2e-evidence.json",
        {"cases": cases[:-1] if fault == "missing_case" else cases},
    )
    assert p6_verify.acceptance() == 1
    assert json.loads((runtime / "acceptance.json").read_text())["passed"] is False


def test_verify_failure_is_not_converted_to_success(tmp_path, monkeypatch):
    monkeypatch.setattr(p6_verify, "ROOT", tmp_path)
    monkeypatch.setattr(p6_verify, "LATEST", tmp_path / ".runtime/latest.json")
    monkeypatch.setattr(p6_verify, "fingerprint", lambda: "hash")
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 1})()

    monkeypatch.setattr(p6_verify.subprocess, "run", fail)
    assert p6_verify.verify() == 1
    pointer = json.loads(p6_verify.LATEST.read_text())
    summary = json.loads(Path(pointer["summary"]).read_text())
    assert summary["passed"] is False
    assert len(calls) == 3 and all(job["exit_code"] == 1 for job in summary["jobs"])
