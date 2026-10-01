"""Owned local P3 process lifecycle. Keeps PostgreSQL and business data intact."""

import json
import os
import secrets
import subprocess
import time
import urllib.request
from pathlib import Path

from demo_db import CONTAINER, VOLUME, inspect_resource
from demo_process import process_identity, stop_owned

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".runtime/demo-processes.json"
PERSONAS = ("customer-a1", "customer-a2", "operator-a", "customer-b1")


def health(port, marker=None, service=None):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            return response.status == 200 and (
                marker is None
                or (
                    response.headers.get("X-WorkflowOps-Startup") == marker
                    and response.headers.get("X-WorkflowOps-Service") == service
                )
            )
    except OSError:
        return False


def validate_record(record, config):
    if (
        record.get("owner") != "workflowops-demo"
        or record.get("marker") != config["marker"]
    ):
        raise ValueError("invalid process startup marker")
    service = record["service"]
    if service not in {"supportops", "harness"}:
        raise ValueError("unknown owned service")
    environment = "supportops-ai" if service == "supportops" else "agentopshub"
    expected = Path("D:/Anaconda3/envs") / environment / "python.exe"
    if Path(record["executable"]).resolve() != expected.resolve():
        raise ValueError("unexpected service executable")


def down():
    if not STATE.exists():
        print("No owned demo service processes; database retained")
        return 0
    state = json.loads(STATE.read_text(encoding="utf-8"))
    path = Path(state["config"]).resolve()
    if (
        not path.is_relative_to(ROOT / ".runtime")
        or state["owner"] != "workflowops-demo"
    ):
        raise ValueError("unowned process state")
    config = json.loads(path.read_text(encoding="utf-8"))
    records = []
    for service in ("harness", "supportops"):
        marker = path.parent / (service + "-process.json")
        if marker.exists():
            record = json.loads(marker.read_text(encoding="utf-8"))
            validate_record(record, config)
            if Path(record["config"]).resolve() != path:
                raise ValueError("process config mismatch")
            actual = process_identity(record["pid"])
            if actual is not None and actual != {key: record[key] for key in actual}:
                raise ValueError("process identity mismatch")
            records.append((marker, record))
    # Prevalidate all processes before stopping any.
    for marker, record in records:
        stop_owned(record)
        marker.unlink(missing_ok=True)
    STATE.unlink()
    print("Owned demo HTTP processes stopped; PostgreSQL and business data retained")
    return 0


def up(port_free, services, test_schema=None):
    if STATE.exists():
        raise ValueError("demo process state exists; use demo-down before restarting")
    if any(not port_free(port) for port in (8010, 8011)):
        raise ValueError("demo HTTP port occupied; no process stopped")
    container = inspect_resource("container", CONTAINER)
    inspect_resource("volume", VOLUME)
    if not container or container["State"].get("Health", {}).get("Status") != "healthy":
        raise ValueError("P2 owned PostgreSQL must already be healthy")
    marker = secrets.token_hex(16)
    directory = ROOT / ".runtime" / ("demo-" + marker)
    directory.mkdir()
    config_path = directory / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "owner": "workflowops-demo",
                "marker": marker,
                "mode": "offline",
                "supportops_tokens": {
                    name: secrets.token_urlsafe(32) for name in PERSONAS
                },
                **({"test_schema": test_schema} if test_schema is not None else {}),
            }
        ),
        encoding="utf-8",
    )
    STATE.write_text(
        json.dumps({"owner": "workflowops-demo", "config": str(config_path)}),
        encoding="utf-8",
    )
    children = []
    try:
        for service in ("supportops", "harness"):
            environment, source, _, _, port = services[service]
            cwd = ROOT / "services" / service
            executable = Path("D:/Anaconda3/envs") / environment / "python.exe"
            env = os.environ.copy()
            env.update(
                PYTHONPATH=str((cwd / source).resolve()),
                PYTHONNOUSERSITE="1",
                PYTHONDONTWRITEBYTECODE="1",
                PYTHONIOENCODING="utf-8",
                WORKFLOWOPS_DEMO_CONFIG=str(config_path),
            )
            with (directory / (service + ".log")).open("w", encoding="utf-8") as log:
                child = subprocess.Popen(
                    [
                        str(executable),
                        "-B",
                        str(ROOT / "scripts/demo_service.py"),
                        service,
                    ],
                    cwd=cwd,
                    env=env,
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            children.append(child)
            for _ in range(100):
                if child.poll() is not None:
                    raise RuntimeError(
                        f"{service} startup failed; inspect ignored local log"
                    )
                if health(port, marker, service):
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("owned demo service did not become healthy")
        print(
            "Offline P3 HTTP APIs ready: Harness 127.0.0.1:8010; SupportOps 127.0.0.1:8011"
        )
        return 0
    except Exception:
        # Popen retains an OS handle to each process started here, immune to PID reuse.
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
        down()
        raise
