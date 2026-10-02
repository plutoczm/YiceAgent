"""P1 subprocess-only service dispatch; no cross-service application imports."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from service_paths import SERVICE_DIRECTORIES

ROOT = Path(__file__).resolve().parents[1]
CONDA = Path(os.environ.get("WORKFLOWOPS_CONDA_EXE", "D:/Anaconda3/Scripts/conda.exe"))
SERVICES = {
    "harness": (
        "agentopshub",
        "backend/src",
        "app",
        [
            "fastapi",
            "sqlalchemy",
            "langgraph",
            "alembic",
            "asyncpg",
            "pydantic",
            "httpx2",
            "pytest",
        ],
        8010,
    ),
    "supportops": (
        "supportops-ai",
        ".",
        "app",
        ["fastapi", "sqlalchemy", "psycopg", "alembic", "pydantic", "pytest"],
        8011,
    ),
    "datacopilot": (
        "datacopilot",
        ".",
        "backend.app",
        ["fastapi", "duckdb", "sqlglot", "langgraph", "pydantic", "pytest"],
        8012,
    ),
}
PROBE_MODULES = {
    "harness": "app.agents.models",
    "supportops": "app.business_data.protocol",
    "datacopilot": "backend.app.infrastructure.business_data.contract_catalog",
}


def service_process(service, arguments, capture=False):
    environment, source_path, _, _, _ = SERVICES[service]
    cwd = ROOT / "services" / SERVICE_DIRECTORIES[service]
    if arguments[:2] == ["-m", "pytest"]:
        arguments = [
            *arguments,
            "-o",
            f"cache_dir={ROOT / '.runtime/pytest-cache' / SERVICE_DIRECTORIES[service]}",
        ]
    env = os.environ.copy()
    # Replace inherited PYTHONPATH; suppress bytecode and user site imports.
    env.update(
        PYTHONPATH=str((cwd / source_path).resolve()),
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
    )
    if service == "datacopilot":
        env["DATACOPILOT_LOGGING__FILE_PATH"] = str(
            ROOT / ".runtime/service-logs/analytics.log"
        )
    command = [
        str(CONDA),
        "run",
        "--no-capture-output",
        "-n",
        environment,
        "python",
        "-B",
        *arguments,
    ]
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        timeout=120 if capture else None,
        check=False,
    )


def probe_service(service):
    _, source_path, package, modules, _ = SERVICES[service]
    # Conda's Windows wrapper rejects multiline command arguments.
    code = "; ".join(
        [
            "import importlib, importlib.util, json, sys",
            "from pathlib import Path",
            f"package = importlib.import_module({package!r})",
            "origin = str(Path(package.__file__).resolve())",
            f"expected = Path({str(ROOT / 'services' / SERVICE_DIRECTORIES[service] / source_path)!r}).resolve()",
            f"required = {modules!r}",
            "missing = [m for m in required if importlib.util.find_spec(m) is None]",
            "loaded = [importlib.import_module(m) for m in required if m not in missing]",
            f"representative = importlib.import_module({PROBE_MODULES[service]!r})",
            "representative_origin = str(Path(representative.__file__).resolve())",
            "assert Path(origin).is_relative_to(expected), 'import escaped service snapshot'",
            (
                "assert Path(representative_origin).is_relative_to(expected), "
                "'submodule escaped snapshot'"
            ),
            (
                "print(json.dumps(dict(executable=sys.executable, version=sys.version.split()[0], "
                "origin=origin, representative_origin=representative_origin, missing=missing)))"
            ),
        ]
    )
    result = service_process(service, ["-c", code], capture=True)
    if result.returncode:
        return {
            "passed": False,
            "error": "service probe failed",
            "returncode": result.returncode,
        }
    report = json.loads(result.stdout.strip().splitlines()[-1])
    environment = SERVICES[service][0]
    expected_interpreter = CONDA.parent.parent / "envs" / environment / "python.exe"
    report["passed"] = (
        not report["missing"]
        and Path(report["executable"]).resolve() == expected_interpreter.resolve()
    )
    return report


def port_free(port):
    try:
        with socket.socket() as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def docker_check():
    executable = shutil.which("docker")
    if executable is None:
        candidate = Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe")
        executable = str(candidate) if candidate.is_file() else None
    if executable is None:
        return {"passed": False, "cli": False, "compose": False, "daemon": False}
    results = {}
    for key, arguments in {
        "cli": ["--version"],
        "compose": ["compose", "version"],
        "daemon": ["info", "--format", "{{.ServerVersion}}"],
    }.items():
        try:
            result = subprocess.run(
                [executable, *arguments],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            results[key] = result.returncode == 0
            if result.returncode == 0:
                results[key + "_version"] = result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            results[key] = False
    results["passed"] = all(results[key] for key in ("cli", "compose", "daemon"))
    return results


def doctor():
    from demo_db import CONTAINER, VOLUME, inspect_resource

    ports = {str(port): port_free(port) for port in (8010, 8011, 8012, 55432)}
    owned_database_ready = False
    if not ports["55432"]:
        try:
            container = inspect_resource("container", CONTAINER)
            volume = inspect_resource("volume", VOLUME)
            owned_database_ready = bool(
                container
                and volume
                and container["State"].get("Health", {}).get("Status") == "healthy"
                and (ROOT / ".runtime/demo-db.json").is_file()
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            owned_database_ready = False
    report = {
        "services": {service: probe_service(service) for service in SERVICES},
        "ports": ports,
        "owned_database_ready": owned_database_ready,
        "docker": docker_check(),
        "model_components_loaded": False,
    }
    report["passed"] = (
        all(r["passed"] for r in report["services"].values())
        and all(ports[str(port)] for port in (8010, 8011, 8012))
        and (ports["55432"] or owned_database_ready)
        and report["docker"]["passed"]
    )
    output = ROOT / ".runtime" / "environment-audit.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("start")
    commands.add_parser("check-contracts")
    verify = commands.add_parser("verify")
    verify.add_argument("--offline", action="store_true", required=True)
    ui = commands.add_parser("demo-check")
    choice = ui.add_mutually_exclusive_group(required=True)
    choice.add_argument("--group", choices=["ui"])
    choice.add_argument("--all", action="store_true")
    up = commands.add_parser("demo-up")
    up.add_argument("--mode", choices=["offline"], default="offline")
    commands.add_parser("demo-down")
    integration = commands.add_parser("test-integration")
    integration.add_argument(
        "--group", choices=["support", "delivery", "reports"], required=True
    )
    seed = commands.add_parser("seed-demo")
    seed.add_argument("--seed", type=int, default=42)
    seed.add_argument("--reset-demo", action="store_true")
    test = commands.add_parser("test-service")
    test.add_argument("service", choices=SERVICES)
    test.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == "verify":
        from p6_verify import verify

        return verify()
    if args.command == "demo-check" and args.all:
        from p6_verify import acceptance

        return acceptance()
    if args.command == "start":
        from demo_runtime import up

        if doctor():
            return 1
        return up(port_free, SERVICES)
    if args.command == "demo-check":
        from p3_integration import run

        result = service_process(
            "harness", ["-m", "pytest", "backend/tests/test_p5_workbench.py", "-q"]
        )
        if result.returncode:
            return result.returncode
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "pytest",
                "tests/test_p5_browser.py",
                "tests/test_p5_interactions.py",
                "-q",
            ],
            cwd=ROOT,
            check=False,
        )
        if result.returncode:
            return result.returncode
        return run(port_free, SERVICES, service_process, group="ui")
    if args.command == "demo-up":
        from demo_runtime import up

        return up(port_free, SERVICES)
    if args.command == "demo-down":
        from demo_runtime import down

        return down()
    if args.command == "test-integration":
        from p3_integration import run

        return run(port_free, SERVICES, service_process, group=args.group)
    if args.command == "doctor":
        return doctor()
    if args.command == "check-contracts":
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts/check_contracts.py")],
            cwd=ROOT,
            check=False,
        ).returncode
    if args.command == "seed-demo":
        from demo_db import ensure_database

        for service in ("datacopilot", "supportops"):
            if not probe_service(service)["passed"]:
                return 1
        result = service_process(
            "datacopilot",
            [
                "scripts/generate_demo_data.py",
                "--seed",
                str(args.seed),
                "--output",
                str(ROOT / ".runtime/demo-fixture"),
            ],
        )
        if result.returncode:
            return result.returncode
        ensure_database(port_free)
        result = service_process("supportops", ["scripts/prepare_demo_db.py"])
        if result.returncode:
            return result.returncode
        return service_process(
            "supportops",
            [
                "scripts/import_demo_fixture.py",
                *(["--reset-demo"] if args.reset_demo else []),
            ],
        ).returncode
    report = probe_service(args.service)
    if not report["passed"]:
        print(json.dumps(report))
        return 1
    pytest_args = args.pytest_args
    if pytest_args[:2] == ["--group", "demo-data"]:
        pytest_args = ["tests/test_demo_data.py", *pytest_args[2:]]
    elif pytest_args[:2] == ["--group", "workflow"] and args.service == "harness":
        pytest_args = [
            "backend/tests/test_p3_workflow.py",
            "backend/tests/test_p4_analytics.py",
            *pytest_args[2:],
        ]
    elif pytest_args[:2] == ["--group", "governed"] and args.service == "datacopilot":
        pytest_args = [
            "tests/application/business_analytics",
            "tests/infrastructure/business_analytics",
            "tests/infrastructure/business_data",
            "tests/application/identity",
            "tests/infrastructure/identity",
            "tests/api/test_business_analytics_dependencies.py",
            "tests/api/test_business_analytics_api.py",
            "tests/test_p4_reports.py",
            *pytest_args[2:],
        ]
    elif pytest_args[:2] == ["--group", "safety"] and args.service == "supportops":
        pytest_args = [
            "tests/test_p3_demo.py",
            "tests/test_p3_database_cleanup.py",
            "tests/test_auth.py",
            "tests/test_tenant_boundary.py",
            "tests/test_business_operations.py",
            "tests/test_ticket_messages.py",
            "tests/test_s1_workflows.py",
            *pytest_args[2:],
        ]
    return service_process(args.service, ["-m", "pytest", *pytest_args]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
