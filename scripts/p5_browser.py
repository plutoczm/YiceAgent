"""P5 test-only browser resources; never reset or fault the main demo schema."""

import argparse
import json
import re
from pathlib import Path
from uuid import uuid4

from demo_process import stop_owned
from demo_runtime import ROOT, STATE, down, up, validate_record
from dev import SERVICES, port_free, service_process

EVIDENCE = ROOT / ".runtime/p5-browser"


def session_evidence(schema):
    """Keep each generated browser session's artifacts separate and recoverable."""
    if re.fullmatch(r"p3_test_[a-f0-9]{32}", schema) is None:
        raise ValueError("invalid browser evidence session")
    return EVIDENCE / schema


def current():
    state = json.loads(STATE.read_text(encoding="utf-8"))
    path = Path(state["config"]).resolve()
    if not path.is_relative_to(ROOT / ".runtime"):
        raise ValueError("unowned browser config")
    config = json.loads(path.read_text(encoding="utf-8"))
    manifest = json.loads((EVIDENCE / "state.json").read_text(encoding="utf-8"))
    if config.get("test_schema") != manifest["schema"]:
        raise ValueError("browser fixture mismatch")
    return config, path.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["prepare", "corrupt", "repair", "stop-support", "finish"]
    )
    args = parser.parse_args()
    if args.action == "prepare":
        if STATE.exists() or (EVIDENCE / "state.json").exists():
            raise ValueError("existing state; refuse overwrite")
        schema = "p3_test_" + uuid4().hex
        EVIDENCE.mkdir(exist_ok=True)
        session_evidence(schema).mkdir()
        (EVIDENCE / "state.json").write_text(
            json.dumps({"schema": schema}), encoding="utf-8"
        )
        try:
            result = service_process(
                "supportops", ["scripts/p3_test_database.py", "create", schema]
            )
            if result.returncode:
                raise RuntimeError("browser fixture preparation failed")
            up(port_free, SERVICES, test_schema=schema)
        except Exception:
            down()
            cleanup = service_process(
                "supportops", ["scripts/p3_test_database.py", "drop", schema]
            )
            if cleanup.returncode:
                raise RuntimeError("browser fixture cleanup failed") from None
            (EVIDENCE / "state.json").unlink()
            raise
        print("P5 isolated 24-order fixture ready; main demo DB unchanged")
        return
    config, directory = current()
    artifacts = session_evidence(config["test_schema"])
    if args.action == "stop-support":
        record = json.loads(
            (directory / "supportops-process.json").read_text(encoding="utf-8")
        )
        validate_record(record, config)
        if Path(record["config"]).resolve() != directory / "config.json":
            raise ValueError("browser process config mismatch")
        stop_owned(record)
        print("Only isolated browser SupportOps stopped for failure demonstration")
        return
    if args.action == "finish":
        down()
        result = service_process(
            "supportops", ["scripts/p3_test_database.py", "drop", config["test_schema"]]
        )
        if result.returncode:
            raise RuntimeError("browser cleanup failed")
        (EVIDENCE / "state.json").rename(artifacts / "finished.json")
        return
    delivery = Path(config["delivery_root"]).resolve()
    if not delivery.is_relative_to(directory):
        raise ValueError("browser test delivery outside isolated directory")
    index = delivery / "tenant-a/current.json"
    publication = json.loads(index.read_text(encoding="utf-8"))
    target = (index.parent / publication["export_id"] / "orders_v1.jsonl").resolve()
    if not target.is_relative_to(delivery) or target.is_symlink():
        raise ValueError("invalid browser test path")
    backup = artifacts / "orders-backup.jsonl"
    if args.action == "corrupt":
        with backup.open("xb") as stream:
            stream.write(target.read_bytes())
        (artifacts / "fault.json").write_text(
            json.dumps({"target": str(target)}), encoding="utf-8"
        )
        target.write_bytes(b"{}\n")
    else:
        fault = json.loads((artifacts / "fault.json").read_text(encoding="utf-8"))
        if Path(fault["target"]).resolve() != target:
            raise ValueError("publication changed during browser fault")
        target.write_bytes(backup.read_bytes())
    print("Isolated snapshot " + args.action + " complete")


if __name__ == "__main__":
    main()
