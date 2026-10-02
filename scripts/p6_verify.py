"""Offline P6 gates with per-job JUnit, evidence and fail-closed AC mapping."""

import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dev import ROOT, SERVICES, port_free, service_process
from p3_integration import run

LATEST = ROOT / ".runtime/p6-latest.json"
SCENARIOS = ROOT / "demo/scenarios/e2e-v1.json"
CHECK_FILES = (
    "scripts/service_paths.py",
    "scripts/demo_runtime.py",
    "scripts/demo_service.py",
    "scripts/import_sources.py",
    "scripts/verify_import.py",
    "scripts/check_contracts.py",
    "scripts/export_p3_contract.py",
    "scripts/export_p4_wire.py",
    "tests/test_p1_boundaries.py",
    "scripts/demo_process.py",
    "scripts/dev.py",
    "scripts/p3_integration.py",
    "scripts/p6_verify.py",
    "tests/test_p6_verify.py",
    "tests/integration/test_demo_v1.py",
)


def fingerprint():
    """Hash actual code/tests and final guide inputs to reject stale reports."""
    digest = hashlib.sha256()
    files = []
    for name in ("scripts", "tests", "services", "contracts", "demo"):
        files.extend(
            p
            for p in (ROOT / name).rglob("*")
            if p.is_file()
            and p.suffix in {".py", ".js", ".cjs", ".json", ".toml", ".html", ".css"}
            and not any(
                part in {"__pycache__", ".pytest_cache", ".ruff_cache"}
                for part in p.parts
            )
        )
    files.extend(ROOT / "docs" / name for name in ("DEMO.md", "INTERVIEW.md"))
    for path in sorted(files):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def junit(path):
    cases = list(ET.parse(path).getroot().iter("testcase"))
    return {
        "passed": sum(
            not any(c.find(t) is not None for t in ("failure", "error", "skipped"))
            for c in cases
        ),
        "failed": sum(
            c.find("failure") is not None or c.find("error") is not None for c in cases
        ),
        "skipped": sum(c.find("skipped") is not None for c in cases),
        "skip_reasons": [
            c.find("skipped").get("message", "")
            for c in cases
            if c.find("skipped") is not None
        ],
    }


def save(path, value):
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def verify():
    directory = ROOT / ".runtime" / ("p6-" + uuid4().hex)
    directory.mkdir(parents=True)
    report = {
        "at": datetime.now(UTC).isoformat(),
        "mode": "offline",
        "fingerprint": fingerprint(),
        "jobs": [],
    }
    save(directory / "summary.json", report)
    save(LATEST, {"summary": str(directory / "summary.json")})

    def execute(name, command=None, service=None, nodes=(), callback=None):
        print(f"P6 gate: {name}", flush=True)
        xml = directory / (name + ".xml")
        arguments = [
            "-m",
            "pytest",
            *nodes,
            "-q",
            "-rs",
            "-o",
            "addopts=",
            "--junitxml=" + str(xml),
        ]
        with (directory / (name + ".log")).open("w", encoding="utf-8") as stream:
            try:
                if callback is not None:
                    code = callback(xml, stream)
                elif service is not None:
                    result = service_process(service, arguments, capture=True)
                    stream.write(result.stdout + result.stderr)
                    code = result.returncode
                else:
                    result = subprocess.run(
                        command or [sys.executable, "-B", *arguments],
                        cwd=ROOT,
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                        check=False,
                    )
                    code = result.returncode
            except (
                OSError,
                ValueError,
                RuntimeError,
                subprocess.SubprocessError,
            ) as exc:
                stream.write(type(exc).__name__ + ": gate raised an exception\n")
                code = 1
        entry = {
            "name": name,
            "exit_code": code,
            "log": str(directory / (name + ".log")),
        }
        if service is not None:
            entry["service"] = service
            entry["environment"] = SERVICES[service][0]
            entry["tests"] = list(nodes)
        if xml.exists():
            entry.update(junit(xml))
            entry["junit"] = str(xml)
        report["jobs"].append(entry)
        save(directory / "summary.json", report)
        print(json.dumps(entry), flush=True)
        return code

    if (ROOT / ".runtime/demo-processes.json").exists():
        report["passed"] = False
        report["error"] = (
            "Stop the currently owned demo with stop.cmd before verify; no active session interrupted"
        )
        save(directory / "summary.json", report)
        print(report["error"])
        return 1
    execute("doctor", [sys.executable, "-B", "scripts/dev.py", "doctor"])
    execute("contracts", [sys.executable, "-B", "scripts/check_contracts.py"])
    execute("source", [sys.executable, "-B", "scripts/verify_import.py"])
    if any(j["exit_code"] for j in report["jobs"]):
        report["passed"] = False
        save(directory / "summary.json", report)
        return 1
    before = service_process(
        "supportops", ["scripts/p6_database_state.py"], capture=True
    )
    if before.returncode:
        report["passed"] = False
        report["error"] = "Owned PostgreSQL baseline check failed"
        save(directory / "summary.json", report)
        return 1
    report["database_before"] = json.loads(before.stdout.strip().splitlines()[-1])
    execute("lint-root", [sys.executable, "-m", "ruff", "check", *CHECK_FILES])
    execute(
        "format-root", [sys.executable, "-m", "ruff", "format", "--check", *CHECK_FILES]
    )
    for action in ("check", "format"):
        execute(
            "supportops-" + action,
            [
                sys.executable,
                "-m",
                "ruff",
                action,
                *(["--check"] if action == "format" else []),
                "--config",
                "services/support/pyproject.toml",
                "services/support/scripts/p6_database_state.py",
            ],
        )
    execute(
        "root",
        nodes=(
            "tests/test_p1_boundaries.py",
            "tests/test_p2_boundaries.py",
            "tests/test_p3_integration_cleanup.py",
            "tests/test_p3_process_boundaries.py",
            "tests/test_p5_start.py",
            "tests/test_p5_browser.py",
            "tests/test_p5_interactions.py",
            "tests/test_p6_verify.py",
        ),
    )
    execute(
        "harness",
        service="harness",
        nodes=(
            "backend/tests/test_p3_workflow.py",
            "backend/tests/test_p4_analytics.py",
            "backend/tests/test_p5_workbench.py",
            "backend/tests/agents",
            "backend/tests/tools/test_runtime.py",
            "backend/tests/tools/test_builtin.py",
            "backend/tests/llm",
        ),
    )
    execute(
        "supportops",
        service="supportops",
        nodes=(
            "tests/test_auth.py",
            "tests/test_tenant_boundary.py",
            "tests/test_business_operations.py",
            "tests/test_ticket_messages.py",
            "tests/test_tickets.py",
            "tests/test_s1_workflows.py",
            "tests/test_workflow.py",
            "tests/test_p3_demo.py",
            "tests/test_p3_database_cleanup.py",
            "tests/test_demo_data.py",
            "tests/test_business_data_contract_v1.py",
            "tests/test_business_data_delivery_v1.py",
            "tests/test_business_data_export_v1.py",
            "tests/test_p4_exports.py",
            "tests/test_p4_snapshot_postgres.py",
        ),
    )
    execute(
        "datacopilot",
        service="datacopilot",
        nodes=(
            "tests/application/business_analytics",
            "tests/infrastructure/business_analytics",
            "tests/infrastructure/business_data",
            "tests/application/identity",
            "tests/infrastructure/identity",
            "tests/api/test_business_analytics_dependencies.py",
            "tests/api/test_business_analytics_api.py",
            "tests/test_p4_reports.py",
            "tests/test_demo_data.py",
            "tests/evaluation/test_business_analytics_golden.py",
        ),
    )

    def evaluate(xml, stream):
        result = service_process(
            "datacopilot",
            [
                "-m",
                "tests.evaluation.run_business_analytics_evaluation",
                "--json-report",
                str(directory / "analytics-evaluation.json"),
            ],
            capture=True,
        )
        stream.write(result.stdout + result.stderr)
        return result.returncode

    execute("analytics-evaluation", callback=evaluate)
    for group in ("support", "delivery", "reports", "ui", "v1"):

        def integration(xml, stream, group=group):
            key = "WORKFLOWOPS_P6_EVIDENCE"
            previous = os.environ.get(key)
            os.environ[key] = str(directory / "e2e-evidence.json")
            try:
                return run(
                    port_free,
                    SERVICES,
                    service_process,
                    group=group,
                    pytest_args=("--junitxml=" + str(xml),),
                    output=stream,
                )
            finally:
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous

        execute("http-" + group, callback=integration)
    after = service_process(
        "supportops", ["scripts/p6_database_state.py"], capture=True
    )
    report["database_after"] = (
        json.loads(after.stdout.strip().splitlines()[-1])
        if after.returncode == 0
        else None
    )
    report["database_unchanged"] = report["database_before"] == report["database_after"]
    report["processes_stopped"] = not (
        ROOT / ".runtime/demo-processes.json"
    ).exists() and all(port_free(p) for p in (8010, 8011, 8012))
    report["passed"] = (
        all(j["exit_code"] == 0 and j.get("failed", 0) == 0 for j in report["jobs"])
        and report["database_unchanged"]
        and report["processes_stopped"]
    )
    save(directory / "summary.json", report)
    print(f"P6 verify passed={report['passed']}; report={directory / 'summary.json'}")
    return 0 if report["passed"] else 1


def acceptance():
    if not LATEST.exists():
        print("No P6 verification evidence; run verify --offline first")
        return 1
    pointer = json.loads(LATEST.read_text(encoding="utf-8"))
    summary = Path(pointer["summary"]).resolve()
    if not summary.is_relative_to(ROOT / ".runtime"):
        raise ValueError("untrusted P6 evidence path")
    report = json.loads(summary.read_text(encoding="utf-8"))
    manifest = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    valid = report.get("passed") is True and report["fingerprint"] == fingerprint()
    jobs = {j["name"]: j for j in report["jobs"]}
    v1 = jobs.get("http-v1", {})
    valid = (
        valid
        and v1.get("passed") == 24
        and v1.get("failed") == 0
        and v1.get("skipped") == 0
    )
    evidence_path = summary.parent / "e2e-evidence.json"
    evidence = (
        json.loads(evidence_path.read_text(encoding="utf-8"))
        if evidence_path.exists()
        else {"cases": []}
    )
    valid = valid and [c["id"] for c in evidence["cases"]] == [
        c["id"] for c in manifest["cases"]
    ]
    mapped = []
    for ac, case_ids in manifest["acceptance"].items():
        row = {
            "id": ac,
            "status": "passed" if valid else "failed",
            "cases": case_ids,
            "runs": [
                r
                for c in evidence["cases"]
                if c["id"] in case_ids
                for r in c["run_ids"]
            ],
        }
        row["tests"] = [
            f"tests/integration/test_demo_v1.py::test_demo_v1[{case_id}]"
            for case_id in case_ids
        ]
        row["junit"] = v1.get("junit")
        if ac == "AC-03":
            row["browser_evidence"] = (
                "docs/P5_VALIDATION.md: six real browser scripts, 2026-10-02; P6 reruns HTTP and JS regression"
            )
        mapped.append(row)
    save(
        summary.parent / "acceptance.json",
        {"passed": bool(valid), "summary": str(summary), "acceptance": mapped},
    )
    for row in mapped:
        print(f"{row['id']}: {row['status']} ({', '.join(row['cases'])})")
    return 0 if valid else 1
