"""DataCopilot 前后端一键生命周期管理器。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"
LOG_DIR = ROOT / "data/logs"
STATE_FILE = RUNTIME / "services.json"
ENV_NAME = "datacopilot"
REQUIRED_IMPORTS = ("chromadb", "fastapi", "streamlit", "pytest")
PUBLIC_URL = "https://datacopilot-orcin.vercel.app/"
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def conda_executable() -> str:
    candidates = [
        os.environ.get("CONDA_EXE"),
        shutil.which("conda"),
        ROOT.anchor + "Anaconda3/Scripts/conda.exe",
        str(Path.home() / "anaconda3/Scripts/conda.exe"),
        str(Path.home() / "miniconda3/Scripts/conda.exe"),
        "C:/ProgramData/Anaconda3/Scripts/conda.exe",
        "/opt/miniconda3/bin/conda",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise RuntimeError("Miniconda/Conda was not found. Install it first or use `python manage.py public`.")


def runtime_environment() -> dict[str, str]:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    paths = {
        "CONDA_PKGS_DIRS": RUNTIME / "conda-pkgs",
        "PIP_CACHE_DIR": RUNTIME / "pip-cache",
        "PYTHONPYCACHEPREFIX": RUNTIME / "pycache",
        "TMP": RUNTIME / "tmp",
        "TEMP": RUNTIME / "tmp",
        "XDG_CACHE_HOME": RUNTIME / "cache",
    }
    env = os.environ.copy()
    for key, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    return env


def environment_prefix(conda: str) -> Path | None:
    result = subprocess.run([conda, "env", "list", "--json"], check=True, capture_output=True, text=True)
    for value in json.loads(result.stdout).get("envs", []):
        prefix = Path(value)
        if prefix.name == ENV_NAME:
            return prefix
    return None


def environment_is_ready(prefix: Path) -> bool:
    python = prefix / ("python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        return False
    imports = "; ".join(f"import {name}" for name in REQUIRED_IMPORTS)
    return subprocess.run([str(python), "-c", imports], capture_output=True).returncode == 0


def ensure_environment() -> Path:
    conda = conda_executable()
    prefix = environment_prefix(conda)
    if prefix is None:
        print(f"Creating Conda environment: {ENV_NAME}")
        subprocess.run([conda, "env", "create", "--file", str(ROOT / "environment.yml"), "--yes"], cwd=ROOT, env=runtime_environment(), check=True)
        prefix = environment_prefix(conda)
    elif not environment_is_ready(prefix):
        print(f"Repairing Conda environment: {ENV_NAME}")
        subprocess.run([conda, "env", "update", "--prefix", str(prefix), "--file", str(ROOT / "environment.yml"), "--prune"], cwd=ROOT, env=runtime_environment(), check=True)
    if prefix is None:
        raise RuntimeError(f"Conda environment {ENV_NAME} was not created.")
    if not environment_is_ready(prefix):
        raise RuntimeError(f"Conda environment {ENV_NAME} is incomplete.")
    return prefix


def python_executable(prefix: Path) -> Path:
    candidate = prefix / ("python.exe" if os.name == "nt" else "bin/python")
    if not candidate.is_file():
        raise RuntimeError(f"Python was not found in {prefix}.")
    return candidate


def process_is_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def stop_pid(pid: int) -> None:
    if not pid or not process_is_running(pid):
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
    else:
        os.killpg(pid, signal.SIGTERM)
    for _ in range(50):
        if not process_is_running(pid):
            return
        time.sleep(0.1)


def wait_until_ready(url: str, process: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Service exited early while waiting for {url}.")
        try:
            with LOCAL_OPENER.open(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.3)
    raise RuntimeError(f"Service did not become ready: {url}")


def start(backend_port: int, frontend_port: int, lan: bool, open_browser: bool) -> None:
    state = read_state()
    running = [pid for pid in state.get("pids", []) if process_is_running(int(pid))]
    if running:
        print(f"DataCopilot is already running: {state.get('frontend_url')}")
        return

    prefix = ensure_environment()
    python = python_executable(prefix)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    host = "0.0.0.0" if lan else "127.0.0.1"
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    child_env = runtime_environment()
    child_env["BACKEND_URL"] = backend_url
    child_env["NO_PROXY"] = "127.0.0.1,localhost"
    child_env["no_proxy"] = child_env["NO_PROXY"]

    backend_log = (LOG_DIR / "backend.log").open("a", encoding="utf-8")
    frontend_log = (LOG_DIR / "frontend.log").open("a", encoding="utf-8")
    backend = subprocess.Popen(
        [str(python), "-m", "uvicorn", "backend.app.main:app", "--host", host, "--port", str(backend_port)],
        cwd=ROOT, env=child_env, stdout=backend_log, stderr=subprocess.STDOUT,
        start_new_session=os.name != "nt",
    )
    frontend: subprocess.Popen | None = None
    try:
        wait_until_ready(f"{backend_url}/health/live", backend)
        frontend = subprocess.Popen(
            [
                str(python), "-m", "streamlit", "run", "frontend/app.py",
                "--server.address", host, "--server.port", str(frontend_port),
                "--server.headless", "true", "--browser.gatherUsageStats", "false",
            ],
            cwd=ROOT, env=child_env, stdout=frontend_log, stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
        wait_until_ready(f"{frontend_url}/_stcore/health", frontend)
    except Exception:
        if frontend is not None:
            stop_pid(frontend.pid)
        stop_pid(backend.pid)
        raise

    STATE_FILE.write_text(
        json.dumps({"pids": [backend.pid, frontend.pid], "backend_url": backend_url, "frontend_url": frontend_url}),
        encoding="utf-8",
    )
    print(f"DataCopilot is ready: {frontend_url}")
    print(f"API docs: {backend_url}/docs")
    print(f"Public URL: {PUBLIC_URL}")
    if open_browser:
        webbrowser.open(frontend_url)


def stop() -> None:
    state = read_state()
    pids = [int(pid) for pid in state.get("pids", [])]
    for pid in reversed(pids):
        stop_pid(pid)
    STATE_FILE.unlink(missing_ok=True)
    print("DataCopilot stopped." if pids else "DataCopilot is not running.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--backend-port", type=int, default=8000)
    start_parser.add_argument("--frontend-port", type=int, default=8502)
    start_parser.add_argument("--lan", action="store_true")
    start_parser.add_argument("--no-open", action="store_true")
    subparsers.add_parser("stop")
    subparsers.add_parser("status")
    subparsers.add_parser("setup")
    subparsers.add_parser("public")
    args = parser.parse_args()
    if args.command == "start":
        start(args.backend_port, args.frontend_port, args.lan, not args.no_open)
    elif args.command == "stop":
        stop()
    elif args.command == "status":
        state = read_state()
        pids = [int(pid) for pid in state.get("pids", [])]
        print(state.get("frontend_url") if pids and all(process_is_running(pid) for pid in pids) else "stopped")
    elif args.command == "setup":
        print(f"Environment ready: {ensure_environment()}")
    else:
        print(PUBLIC_URL)
        webbrowser.open(PUBLIC_URL)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
