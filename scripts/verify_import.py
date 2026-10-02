"""Read-only P1 import acceptance: hashes, source Git state and file boundaries."""

import json
from pathlib import Path

from import_sources import ROOT, safe_path, sha, snapshot
from service_paths import SERVICE_DIRECTORIES, relocated_path


def verify():
    manifest = json.loads(
        (ROOT / "docs/source-manifest.json").read_text(encoding="utf-8")
    )
    counts = {}
    for item in manifest["files"]:
        source = safe_path(Path(item["source_repo"]), item["source_path"])
        target = safe_path(ROOT, relocated_path(item["target_path"]))
        if sha(source) != item["sha256"] or sha(target) != item["sha256"]:
            raise ValueError(f"hash mismatch: {item['target_path']}")
        counts[item["service"]] = counts.get(item["service"], 0) + 1
    for service, before in manifest["repositories"].items():
        if snapshot(Path(before["source_repo"])) != before:
            raise ValueError(f"source Git state changed: {service}")
    for service in manifest["repositories"]:
        service_root = ROOT / "services" / SERVICE_DIRECTORIES[service]
        for path in service_root.rglob("*"):
            if path.name == ".git":
                raise ValueError("nested Git metadata")
            if path.is_file() and path.suffix.lower() in {
                ".db",
                ".sqlite",
                ".sqlite3",
                ".pem",
                ".key",
            }:
                raise ValueError("database/key file in service snapshot")
            if path.is_file() and path.name.startswith(".env"):
                raise ValueError("environment file in service snapshot")
        if not (service_root / "LICENSE").is_file():
            raise ValueError("source license missing")
    report = {
        "passed": True,
        "verified_files": len(manifest["files"]),
        "counts": counts,
        "source_git_unchanged": True,
        "nested_git": False,
        "secrets_and_databases_copied": False,
        "root_git_initialized": (ROOT / ".git").exists(),
    }
    output = ROOT / ".runtime/import-audit.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    verify()
