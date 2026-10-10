"""Reproducible checks, explicitly approved live calls, and frozen acceptance evidence."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime/v2"
MANIFEST = ROOT / "docs/validation/acceptance-manifest.json"


def call(args, env=None):
    return subprocess.run(args, cwd=ROOT, env=env, check=False).returncode


def checks():
    output = RUNTIME / "phase5"
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(YICE_AGENT_INTEGRATION="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    commands = [
        ("ruff", [sys.executable, "-B", "-m", "ruff", "check", "src", "tests", "scripts"]),
        (
            "format",
            [sys.executable, "-B", "-m", "ruff", "format", "--check", "src", "tests", "scripts"],
        ),
        ("configured_mypy", [sys.executable, "-B", "-m", "mypy"]),
        (
            "python_real_owned_integration",
            [
                sys.executable,
                "-B",
                "-m",
                "pytest",
                "tests",
                "-q",
                "--junitxml=" + str(output / "tests.xml"),
            ],
        ),
        (
            "node_auth_and_ui",
            [
                "node",
                "--test",
                "tests/unit/test_oauth_input.mjs",
                "tests/unit/test_oauth_errors.mjs",
                "tests/unit/test_ui_controls.mjs",
            ],
        ),
        ("historical_sources", [sys.executable, "-B", "scripts/maintenance/verify_layout.py"]),
        ("diff_whitespace", ["git", "diff", "--check"]),
    ]
    commands += [
        (str(p.relative_to(ROOT)), ["node", "--check", str(p)])
        for p in sorted((ROOT / "src/yice_agent/static").glob("*.js"))
    ]
    rows = [{"check": name, "exit_code": call(args, env)} for name, args in commands]
    xml = ElementTree.parse(output / "tests.xml")
    suites = list(xml.getroot().iter("testsuite"))
    totals = {
        key: sum(int(s.get(key, "0")) for s in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    passed = all(r["exit_code"] == 0 for r in rows) and totals["skipped"] == 0
    report = {
        "status": "PASS" if passed else "FAILED",
        "at": datetime.now(UTC).isoformat(),
        "checks": rows,
        "python": totals,
        "model_calls": 0,
    }
    (output / "checks.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if passed else 1


def evidence():
    if not MANIFEST.exists():
        print(json.dumps({"status": "NOT_RUN", "reason": "frozen_manifest_missing"}))
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures = []
    for row in manifest["artifacts"] + manifest["source_files"]:
        p = (ROOT / row["path"]).resolve()
        if not p.is_relative_to(ROOT) or not p.is_file():
            failures.append(row["path"] + ":missing")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != row["sha256"]:
            failures.append(row["path"] + ":changed")
    gates = manifest["gates"]
    for name, gate in gates.items():
        if gate["required"] and gate["status"] != "PASS":
            failures.append(name + ":" + gate["status"])
    status = "PASS" if not failures else "FAILED"
    report = {
        "status": status,
        "at": datetime.now(UTC).isoformat(),
        "failures": failures,
        "gates": gates,
        "model_calls": 0,
        "scope": "frozen_local_evidence_integrity",
    }
    (RUNTIME / "phase5/evidence-check.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report))
    return 0 if not failures else 1


def live(approved, channel):
    if not approved:
        print("explicit_live_approval_required")
        return 1
    # Pricing, persistent reservation and cap are checked by Gateway on each dispatch.
    commands = [
        [
            sys.executable,
            "-B",
            "scripts/validation/business_agent.py",
            "--approved",
            "--channel",
            channel,
        ],
        [
            sys.executable,
            "-B",
            "scripts/validation/adversarial.py",
            "--approved",
            "--channel",
            channel,
        ],
    ]
    channels = ("deepseek", "chatgpt", "qwen") if channel == "all" else (channel,)
    commands += [
        [
            sys.executable,
            "-B",
            "scripts/validation/context_memory.py",
            "--approved",
            "--phase",
            "5",
            "--turns",
            "30",
            "--channel",
            c,
        ]
        for c in channels
    ]
    commands += [
        [sys.executable, "-B", "scripts/validation/memory_switch.py", "--approved", "--phase", "5"]
    ]
    for args in commands:
        result = call(args)
        if result:
            return result
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["checks", "live", "evidence"], nargs="?", default="checks")
    p.add_argument("--approved", action="store_true")
    p.add_argument("--channel", choices=["all", "deepseek", "chatgpt", "qwen"], default="all")
    a = p.parse_args()
    if a.mode == "live":
        return live(a.approved, a.channel)
    return checks() if a.mode == "checks" else evidence()


if __name__ == "__main__":
    raise SystemExit(main())
