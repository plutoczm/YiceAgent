"""Run real HTTP tests in an owned disposable PG schema; restore demo processes."""

import subprocess
import sys
from uuid import uuid4

from demo_runtime import ROOT, STATE, down, up


def run(
    port_free, services, service_process, group="support", pytest_args=(), output=None
):
    restore = STATE.exists()
    schema = "p3_test_" + uuid4().hex
    if restore:
        down()
    prepare_attempted = False
    try:
        prepare_attempted = True
        result = service_process(
            "supportops", ["scripts/p3_test_database.py", "create", schema]
        )
        if result.returncode:
            return result.returncode
        up(port_free, services, test_schema=schema)
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "pytest",
                "tests/integration/test_p3_support.py"
                if group == "support"
                else "tests/integration/test_demo_v1.py"
                if group == "v1"
                else "tests/integration/test_p5_ui.py"
                if group == "ui"
                else "tests/integration/test_p4_" + group + ".py",
                "-q",
                *pytest_args,
            ],
            cwd=ROOT,
            check=False,
            stdout=output,
            stderr=subprocess.STDOUT if output is not None else None,
        ).returncode
    finally:
        down()
        if prepare_attempted:
            cleanup = service_process(
                "supportops", ["scripts/p3_test_database.py", "drop", schema]
            )
            if cleanup.returncode:
                raise RuntimeError("P3 test schema cleanup failed")
        if restore:
            up(port_free, services)
