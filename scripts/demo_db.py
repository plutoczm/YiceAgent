"""Docker ownership guard for P2. No arbitrary DSN or resource selection."""

import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = "workflowops-demo"
CONTAINER = "workflowops-demo-postgres-1"
VOLUME = "workflowops-demo_workflowops_demo_pgdata"
IMAGE = "postgres:16.13-trixie"


def docker(*args, env=None, check=True):
    executable = (
        shutil.which("docker")
        or "C:/Program Files/Docker/Docker/resources/bin/docker.exe"
    )
    return subprocess.run(
        [executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=check,
        timeout=300,
    )


def inspect_resource(kind, name):
    inventory = docker(
        kind,
        "ls",
        "-q" if kind == "container" else "--quiet",
        *(["-a", "--filter", f"name=^/{name}$"] if kind == "container" else []),
    )
    names = inventory.stdout.splitlines()
    if kind == "volume" and name not in names:
        return None
    if kind == "container" and not names:
        return None
    value = json.loads(docker(kind, "inspect", name).stdout)[0]
    labels = (
        value["Config"].get("Labels", {})
        if kind == "container"
        else value.get("Labels", {})
    )
    if (
        labels.get("com.workflowops.owner") != OWNER
        or labels.get("com.docker.compose.project") != OWNER
    ):
        raise ValueError(f"unowned {kind}; refusing reuse")
    if kind == "container":
        if value["Config"]["Image"] != IMAGE:
            raise ValueError("unexpected database image")
        mounts = value["Mounts"]
        if not any(
            m.get("Name") == VOLUME and m["Destination"] == "/var/lib/postgresql/data"
            for m in mounts
        ):
            raise ValueError("unexpected database volume")
        ports = value["HostConfig"]["PortBindings"]
        if ports != {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "55432"}]}:
            raise ValueError("unexpected database binding")
    return value


def ensure_database(port_free):
    container = inspect_resource("container", CONTAINER)
    volume = inspect_resource("volume", VOLUME)
    path = ROOT / ".runtime/demo-db.json"
    if path.exists():
        config = json.loads(path.read_text(encoding="utf-8"))
        if config.get("owner") != OWNER:
            raise ValueError("unowned runtime configuration")
        password = config["password"]
        expected_url = (
            f"postgresql+psycopg://workflowops_demo:{password}"
            "@127.0.0.1:55432/workflowops_demo"
        )
        if config.get("database_url") != expected_url:
            raise ValueError("runtime database configuration drift")
    else:
        if container is not None or volume is not None:
            raise ValueError("existing resources without owned runtime configuration")
        if not port_free(55432):
            raise ValueError("demo database port occupied")
        password = secrets.token_hex(24)
        config = {
            "owner": OWNER,
            "password": password,
            "database_url": f"postgresql+psycopg://workflowops_demo:{password}"
            "@127.0.0.1:55432/workflowops_demo",
        }
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    if container is None and not port_free(55432):
        raise ValueError("demo database port occupied")
    env = os.environ.copy()
    env["WORKFLOWOPS_DEMO_DB_PASSWORD"] = password
    # Startup output is intentionally not echoed: configuration may contain credentials.
    result = docker(
        "compose",
        "--project-name",
        OWNER,
        "-f",
        str(ROOT / "infra/compose.demo.yml"),
        "up",
        "-d",
        "postgres",
        env=env,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("demo PostgreSQL startup failed (credential-safe error)")
    for _ in range(60):
        value = inspect_resource("container", CONTAINER)
        inspect_resource("volume", VOLUME)
        if value and value["State"].get("Health", {}).get("Status") == "healthy":
            print("WorkflowOps-owned PostgreSQL healthy at 127.0.0.1:55432")
            return
        time.sleep(1)
    raise RuntimeError("demo PostgreSQL did not become healthy")
