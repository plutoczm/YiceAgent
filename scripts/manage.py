"""Owned 忆策 Agent bootstrap. Mutations require an explicit approved flag."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
sys.path.insert(0, str(SOURCE))
from yice_agent.config import CONFIG, OWNER, RUNTIME, Settings, initialize_settings  # noqa: E402

MAIN_PYTHON = RUNTIME / "envs/main/Scripts/python.exe"
INFERENCE_PYTHON = RUNTIME / "envs/inference/Scripts/python.exe"
COMPOSE = ROOT / "infra/compose.yml"
RESOURCES = {
    "postgres": (
        "workflowops-v2-postgres-1",
        "workflowops-v2_pgdata",
        "postgres:16.13-trixie",
        55433,
        "5432/tcp",
        "/var/lib/postgresql/data",
    ),
    "qdrant": (
        "workflowops-v2-qdrant-1",
        "workflowops-v2_qdrantdata",
        "qdrant/qdrant:v1.19.0",
        6335,
        "6333/tcp",
        "/qdrant/storage",
    ),
}


def port_free(port):
    try:
        with socket.socket() as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def docker(*args, env=None):
    executable = shutil.which("docker") or "D:/Docker/resources/bin/docker.exe"
    result = subprocess.run(
        [executable, *args], env=env, capture_output=True, text=True, encoding="utf-8", timeout=300
    )
    if result.returncode:
        raise RuntimeError("docker_command_failed")
    return result.stdout


def validate_resource(value, service, *, volume=False):
    name, volume_name, image, port, internal, destination = RESOURCES[service]
    labels = value.get("Labels", {}) if volume else value.get("Config", {}).get("Labels", {})
    if (
        labels.get("com.workflowops.owner") != OWNER
        or labels.get("com.docker.compose.project") != OWNER
    ):
        raise ValueError("resource_owner_mismatch")
    if volume:
        if value.get("Name") != volume_name:
            raise ValueError("volume_name_mismatch")
        return
    if value.get("Name") != "/" + name or value["Config"]["Image"] != image:
        raise ValueError("container_identity_mismatch")
    if value["HostConfig"]["PortBindings"] != {
        internal: [{"HostIp": "127.0.0.1", "HostPort": str(port)}]
    }:
        raise ValueError("container_binding_mismatch")
    mounts = value.get("Mounts", [])
    if not any(
        m.get("Name") == volume_name and m.get("Destination") == destination for m in mounts
    ):
        raise ValueError("container_mount_mismatch")


def inventory():
    containers = set(docker("container", "ls", "-a", "--format", "{{.Names}}").splitlines())
    volumes = set(docker("volume", "ls", "--format", "{{.Name}}").splitlines())
    found = {}
    for service, (name, volume_name, *_) in RESOURCES.items():
        found[service] = None
        if name in containers:
            value = json.loads(docker("container", "inspect", name))[0]
            validate_resource(value, service)
            found[service] = value
        if volume_name in volumes:
            value = json.loads(docker("volume", "inspect", volume_name))[0]
            validate_resource(value, service, volume=True)
        if not CONFIG.exists() and (name in containers or volume_name in volumes):
            raise ValueError("resources_without_owned_configuration")
    return found


def approval_required(approved):
    if not approved:
        raise ValueError("explicit_approval_required")


def install(approved):
    approval_required(approved)
    # Only isolated project venvs. Existing Conda environments remain untouched.
    env = os.environ.copy()
    for key in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST", "PIP_FIND_LINKS"):
        env.pop(key, None)
    env.update(
        PIP_CONFIG_FILE=os.devnull,
        PIP_CACHE_DIR=str(RUNTIME / "pip-cache"),
        NPM_CONFIG_USERCONFIG=os.devnull,
        NPM_CONFIG_GLOBALCONFIG=os.devnull,
        NPM_CONFIG_REGISTRY="https://registry.npmjs.org",
        NPM_CONFIG_CACHE=str(RUNTIME / "npm-cache"),
    )
    for path in (MAIN_PYTHON, INFERENCE_PYTHON):
        directory = path.parents[1]
        if directory.exists() and not path.is_file():
            raise ValueError("incomplete_environment_requires_review")
        if not path.is_file():
            subprocess.run(
                [
                    "D:/Anaconda3/envs/agentopshub/python.exe",
                    "-B",
                    "-m",
                    "venv",
                    "--copies",
                    str(directory),
                ],
                check=True,
                env=env,
            )
    main_lock = ROOT / "requirements/main.lock.txt"
    inference_lock = ROOT / "requirements/inference.lock.txt"
    main_command = [str(MAIN_PYTHON), "-m", "pip", "install"]
    if main_lock.is_file():
        main_command.extend(["-r", str(main_lock)])
    main_command.extend(["-e", str(ROOT) + "[dev]"])
    subprocess.run(main_command, check=True, env=env)
    # Preserve the tested CUDA/CPU wheel variant from the project lock.
    torch_requirement = "torch==2.14.1+cu130"
    if inference_lock.is_file():
        torch_requirement = next(
            line
            for line in inference_lock.read_text(encoding="utf-8").splitlines()
            if line.startswith("torch==")
        )
    torch_platform = "cu130" if "+cu130" in torch_requirement else "cpu"
    if torch_platform == "cu130" and torch_requirement == "torch==2.14.1+cu130":
        # The official large wheel is fetched with range resume and a mandatory SHA-256.
        subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts/models/download_torch.py")],
            check=True,
            env=env,
        )
        wheel = RUNTIME / "wheels/torch-2.14.1+cu130-cp312-cp312-win_amd64.whl"
        subprocess.run(
            [str(INFERENCE_PYTHON), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
            check=True,
            env=env,
        )
    else:
        subprocess.run(
            [
                str(INFERENCE_PYTHON),
                "-m",
                "pip",
                "install",
                "--index-url",
                "https://download.pytorch.org/whl/" + torch_platform,
                torch_requirement,
            ],
            check=True,
            env=env,
        )
    requirements = (
        inference_lock if inference_lock.is_file() else ROOT / "requirements/inference.txt"
    )
    subprocess.run(
        [str(INFERENCE_PYTHON), "-m", "pip", "install", "-r", str(requirements)],
        check=True,
        env=env,
    )
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm_not_found")
    # Windows npm.cmd needs cmd, but no destructive or dynamic filesystem command is composed.
    subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            npm,
            "ci" if (ROOT / "bridge/package-lock.json").is_file() else "install",
            "--ignore-scripts",
        ],
        cwd=ROOT / "bridge",
        check=True,
        env=env,
    )


def infra_up(approved):
    approval_required(approved)
    found = inventory()
    for service, (_, _, _, port, *_) in RESOURCES.items():
        if found[service] is None and not port_free(port):
            raise ValueError("v2_port_occupied")
    initialize_settings()
    settings = Settings.load()
    env = os.environ.copy()
    env["YICE_AGENT_DB_PASSWORD"] = settings.database_password.get_secret_value()
    env["YICE_AGENT_QDRANT_KEY"] = settings.qdrant_key.get_secret_value()
    docker("compose", "--project-name", OWNER, "-f", str(COMPOSE), "up", "-d", env=env)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        resources = inventory()
        pg = resources["postgres"]
        if pg and pg.get("State", {}).get("Health", {}).get("Status") == "healthy":
            break
        time.sleep(1)
    else:
        raise RuntimeError("v2_postgres_not_healthy")
    if not MAIN_PYTHON.is_file():
        raise RuntimeError("v2_environment_missing")
    env["PYTHONPATH"] = str(SOURCE)
    command = "from yice_agent.config import Settings; from yice_agent.database import make_engine,initialize_database; e=make_engine(Settings.load()); initialize_database(e); e.dispose()"
    subprocess.run([str(MAIN_PYTHON), "-B", "-c", command], env=env, check=True)
    print("Owned v2 PostgreSQL initialized; Qdrant allocated; v1 resources retained")


def doctor():
    report = {
        "owner": OWNER,
        "main_environment": MAIN_PYTHON.is_file(),
        "inference_environment": INFERENCE_PYTHON.is_file(),
        "pi_dependency_installed": (
            ROOT / "bridge/node_modules/@earendil-works/pi-ai/package.json"
        ).is_file(),
        "configuration_present": CONFIG.is_file(),
        "model_validation": "NOT_RUN",
    }
    if CONFIG.is_file():
        report["configuration"] = Settings.load().safe_report()
    report["ports_free"] = {str(p): port_free(p) for p in (8020, 55433, 6335)}
    report["infrastructure_ready"] = False
    if CONFIG.is_file():
        try:
            values = inventory()
            pg = values["postgres"]
            qdrant = values["qdrant"]
            report["infrastructure_ready"] = bool(
                pg
                and pg["State"].get("Health", {}).get("Status") == "healthy"
                and qdrant
                and qdrant["State"].get("Running")
            )
            if report["infrastructure_ready"]:
                import httpx

                with httpx.Client(
                    base_url="http://127.0.0.1:6335",
                    headers={"api-key": Settings.load().qdrant_key.get_secret_value()},
                    trust_env=False,
                    timeout=3,
                ) as client:
                    report["infrastructure_ready"] = client.get("/collections").status_code == 200
        except Exception:
            report["infrastructure_ready"] = False
    report["accelerator"] = {"status": "unavailable"}
    if INFERENCE_PYTHON.is_file():
        try:
            env = os.environ.copy()
            env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
            preference = Settings.load().inference_device if CONFIG.is_file() else "auto"
            probe = subprocess.run(
                [
                    str(INFERENCE_PYTHON),
                    "-B",
                    str(ROOT / "scripts/models/verify_accelerator.py"),
                    "--device",
                    preference,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                env=env,
            )
            if probe.returncode == 0:
                report["accelerator"] = json.loads(probe.stdout)
            else:
                report["accelerator"] = {"status": "probe_failed"}
        except Exception:
            report["accelerator"] = {"status": "probe_failed"}
    manifest_path = RUNTIME / "models/manifest.json"
    report["bge_weights_prepared"] = False
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        report["bge_weights_prepared"] = bool(
            manifest.get("owner") == OWNER
            and {m.get("model_id") for m in manifest.get("models", [])}
            == {"BAAI/bge-m3", "BAAI/bge-reranker-v2-m3"}
        )
    print(json.dumps(report, indent=2))
    return (
        0
        if all(
            report[k]
            for k in (
                "main_environment",
                "inference_environment",
                "pi_dependency_installed",
                "configuration_present",
                "infrastructure_ready",
            )
        )
        else 1
    )


def check_baseline():
    # Old versions are immutable Git trees, not parallel source directories.
    from maintenance.verify_layout import verify

    report = verify(ROOT)
    print(json.dumps(report, ensure_ascii=False))
    return 0


def check(borrow_env=False):
    executable = sys.executable if borrow_env else str(MAIN_PYTHON)
    if not borrow_env and not MAIN_PYTHON.is_file():
        raise RuntimeError("v2_environment_missing")
    env = os.environ.copy()
    env.update(
        PYTHONPATH=str(SOURCE),
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
    )
    args = [
        executable,
        "-B",
        "-m",
        "pytest",
        "-c",
        str(ROOT / "pyproject.toml"),
        str(ROOT / "tests"),
        "-q",
    ]
    result = subprocess.run(args, cwd=ROOT, env=env, check=False)
    if result.returncode:
        return result.returncode
    return check_baseline()


def up():
    import secrets
    import urllib.request

    from process_control import process_identity

    if not MAIN_PYTHON.is_file() or not CONFIG.is_file():
        raise RuntimeError("v2_environment_or_configuration_missing")
    from yice_agent.database import check_database, make_engine

    engine = make_engine(Settings.load())
    try:
        if not check_database(engine):
            raise ValueError("v2_database_owner_missing")
    finally:
        engine.dispose()
    state_path = RUNTIME / "app-process.json"
    if state_path.exists():
        raise ValueError("owned_process_state_exists")
    if not port_free(8020):
        raise ValueError("v2_app_port_occupied")
    marker = secrets.token_hex(16)
    env = os.environ.copy()
    env.update(
        PYTHONPATH=str(SOURCE),
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
        YICE_AGENT_STARTUP_MARKER=marker,
    )
    with (RUNTIME / "app.log").open("a", encoding="utf-8") as log:
        child = subprocess.Popen(
            [str(MAIN_PYTHON), "-B", "-m", "yice_agent.server"],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    try:
        record = process_identity(child.pid)
        if record is None:
            raise RuntimeError("v2_app_start_failed")
        record.update(owner=OWNER, marker=marker)
        with state_path.open("x", encoding="utf-8") as stream:
            json.dump(record, stream)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("v2_app_start_failed")
            try:
                with opener.open("http://127.0.0.1:8020/health", timeout=1) as response:
                    if response.headers.get("X-Yice-Agent-V2-Startup") == marker:
                        print("忆策 Agent: http://127.0.0.1:8020")
                        return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError("v2_app_health_timeout")
    except Exception:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        state_path.unlink(missing_ok=True)
        raise


def down():
    from process_control import stop_owned

    path = RUNTIME / "app-process.json"
    if not path.exists():
        print("No owned v2 HTTP process; databases retained")
        return
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("owner") != OWNER or Path(value["executable"]).resolve() != MAIN_PYTHON.resolve():
        raise ValueError("v2_process_owner_mismatch")
    stop_owned(value)
    path.unlink()
    print("Owned v2 HTTP stopped; PostgreSQL, Qdrant and models retained")


def business_prepare(approved):
    approval_required(approved)
    inventory()
    from yice_agent.business.bootstrap import prepare
    from yice_agent.database import make_engine

    engine = make_engine(Settings.load())
    try:
        result = prepare(engine)
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False))


def business_replay(approved):
    approval_required(approved)
    from yice_agent.business.scenarios import replay
    from yice_agent.business.service import BusinessService
    from yice_agent.database import make_engine

    engine = make_engine(Settings.load())
    try:
        result = replay(BusinessService(engine))
    finally:
        engine.dispose()
    path = RUNTIME / ("business-replay-" + result["run_id"] + ".json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "run_id": result["run_id"],
                "scenarios": len(result["scenarios"]),
                "source": result["source"],
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "infra-plan",
            "install",
            "infra-up",
            "business-prepare",
            "replay-demo",
            "doctor",
            "up",
            "down",
            "check",
            "verify",
            "baseline",
        ],
    )
    parser.add_argument(
        "--approved",
        action="store_true",
        help="Use only after the human approves the reviewed external actions",
    )
    parser.add_argument(
        "--borrow-env",
        action="store_true",
        help="Temporary source-only verification using the calling interpreter",
    )
    args = parser.parse_args()
    try:
        if args.command == "infra-plan":
            print(
                json.dumps(
                    {
                        "owner": OWNER,
                        "environments": [
                            str(p.parents[1]) for p in (MAIN_PYTHON, INFERENCE_PYTHON)
                        ],
                        "node_package": "@earendil-works/pi-ai@1.0.0",
                        "resources": {
                            k: {"container": v[0], "volume": v[1], "image": v[2], "port": v[3]}
                            for k, v in RESOURCES.items()
                        },
                        "downloads": ["Python/Node dependencies", "Qdrant image if absent"],
                        "separate_future_gate": [
                            "BGE model weights",
                            "interactive ChatGPT login",
                            "DeepSeek credentials and paid calls",
                        ],
                        "mutations_performed": False,
                    },
                    indent=2,
                )
            )
        elif args.command == "install":
            install(args.approved)
        elif args.command == "infra-up":
            infra_up(args.approved)
        elif args.command == "business-prepare":
            business_prepare(args.approved)
        elif args.command == "replay-demo":
            business_replay(args.approved)
        elif args.command == "doctor":
            return doctor()
        elif args.command == "up":
            up()
        elif args.command == "down":
            down()
        elif args.command == "check":
            return check(args.borrow_env)
        elif args.command == "verify":
            return subprocess.call(
                [str(MAIN_PYTHON), "-B", str(ROOT / "scripts/validation/verify.py"), "checks"],
                cwd=ROOT,
            )
        else:
            return check_baseline()
        return 0
    except Exception as error:
        # Fixed error category, never raw HTTP/subprocess/settings exception bodies.
        known = {
            "explicit_approval_required",
            "docker_command_failed",
            "resource_owner_mismatch",
            "volume_name_mismatch",
            "container_identity_mismatch",
            "container_binding_mismatch",
            "container_mount_mismatch",
            "resources_without_owned_configuration",
            "v2_port_occupied",
            "incomplete_environment_requires_review",
            "npm_not_found",
            "v2_postgres_not_healthy",
            "v2_environment_missing",
            "v2_environment_or_configuration_missing",
            "v2_database_owner_missing",
            "owned_process_state_exists",
            "v2_app_port_occupied",
            "v2_app_start_failed",
            "v2_app_health_timeout",
            "v2_process_owner_mismatch",
            "database_not_owned_v2",
            "database_ownership_missing",
            "database_ownership_invalid",
        }
        code = str(error) if str(error) in known else type(error).__name__
        print(json.dumps({"error": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
