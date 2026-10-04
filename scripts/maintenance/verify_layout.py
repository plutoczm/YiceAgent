"""Verify exact historical Git blobs and optional frozen migration outputs."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


def _relative(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith(("/", "\\"))
        or ":" in value
        or "\\" in value
        or ".." in value.split("/")
        or "\n" in value
        or "\r" in value
        or "\x00" in value
    ):
        raise ValueError("invalid_manifest_path")
    return value


def verify(root: Path, *, strict: bool = False) -> dict[str, Any]:
    root = root.resolve()
    path = root / ".planning/directory-normalization/manifest.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    commit = record["source_commit"]
    if not isinstance(commit, str) or re.fullmatch(r"[a-f0-9]{40}", commit) is None:
        raise ValueError("invalid_snapshot_commit")
    entries = record["entries"]
    before_paths = [_relative(e["before"]) for e in entries]
    if not entries or len(before_paths) != len(set(before_paths)):
        raise ValueError("invalid_manifest_entries")
    command = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=root,
        input="".join(commit + ":" + n + "\n" for n in before_paths).encode(),
        capture_output=True,
        check=False,
    )
    if command.returncode:
        raise ValueError("historical_git_read_failed")
    offset = 0
    current = 0
    for item in entries:
        ending = command.stdout.find(b"\n", offset)
        if ending == -1:
            raise ValueError("historical_git_frame_invalid")
        fields = command.stdout[offset:ending].split()
        if len(fields) != 3 or fields[1] != b"blob" or not fields[2].isdigit():
            raise ValueError("historical_git_blob_missing")
        size = int(fields[2])
        start = ending + 1
        payload = command.stdout[start : start + size]
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != item["before_sha256"]:
            raise ValueError("historical_git_hash_mismatch")
        offset = start + size + 1
        if item.get("after"):
            _relative(item["after"])
            current += 1
            if strict:
                target = (root / item["after"]).resolve()
                if (
                    not target.is_relative_to(root)
                    or not target.is_file()
                    or hashlib.sha256(target.read_bytes()).hexdigest() != item.get("after_sha256")
                ):
                    raise ValueError("current_layout_hash_mismatch")
    if offset != len(command.stdout):
        raise ValueError("historical_git_trailing_data")
    if strict:
        for item in record.get("new_files", []):
            target = (root / _relative(item["path"])).resolve()
            if (
                not target.is_relative_to(root)
                or not target.is_file()
                or hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]
            ):
                raise ValueError("new_layout_hash_mismatch")
    return {
        "historical_files_verified": len(entries),
        "mapped_current_files": current,
        "archived_files": len(entries) - current,
        "snapshot_commit": commit,
        "strict_current_check": strict,
        "result": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Check the frozen migration outputs")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    print(json.dumps(verify(root, strict=args.strict), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
