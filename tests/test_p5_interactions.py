"""Run actual JS handlers with controlled DOM/HTTP doubles; no browser install."""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workbench_reply_interaction_regressions():
    node = os.environ.get("WORKFLOWOPS_NODE_EXE") or shutil.which("node")
    if node is None:
        node = str(
            Path.home()
            / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
        )
    result = subprocess.run(
        [node, str(ROOT / "tests/ui/p5_interactions.cjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "10 workbench interaction regression scenarios passed" in result.stdout
