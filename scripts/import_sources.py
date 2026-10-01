"""Import reviewed working-tree snapshots without changing source repositories."""

import argparse
import hashlib
import json
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "harness": Path("D:/Projects/AgentOpsHub"),
    "supportops": Path("D:/Projects/supportops-ai"),
    "datacopilot": Path("D:/Projects/DataCopilot"),
}
TREES = {
    "backend",
    "app",
    "tests",
    "scripts",
    "docs",
    "contracts",
    "migrations",
    "evaluation",
    "evals",
    "examples",
    "frontend",
    "web",
    "knowledge_base",
    "training",
    ".github",
}
SUFFIXES = {
    ".py",
    ".md",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".txt",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".sql",
    ".mako",
    ".sh",
    ".cmd",
    ".lock",
    ".svg",
}
ROOT_FILES = {
    "LICENSE",
    "Makefile",
    "Dockerfile",
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    ".vercelignore",
    ".python-version",
    ".pre-commit-config.yaml",
}
BLOCKED = {
    ".git",
    ".env",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".runtime",
    ".artifacts",
    "artifacts",
    ".tools",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "unsloth_compiled_cache",
    "dist",
    "build",
    "logs",
    "cache",
}


def git(source, *args):
    return subprocess.check_output(
        ["git", "-C", str(source), *args], encoding="utf-8"
    ).strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(root, relative):
    """Reject links/reparse points in every existing path component."""
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe relative path")
    current = root
    for part in ("", *relative.parts):
        current = current / part
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 1024
            ):
                raise ValueError(f"link/reparse point: {current}")
    if not current.resolve().is_relative_to(root.resolve()):
        raise ValueError("path escaped root")
    return current


def allowed(relative):
    p = Path(relative)
    if p.parts[0] == "models":
        return False
    if any(part.lower() in BLOCKED or part.endswith(".egg-info") for part in p.parts):
        return False
    if any(part.lower().startswith(".env") for part in p.parts):
        return False
    if p.suffix.lower() in {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".db",
        ".sqlite",
        ".sqlite3",
    }:
        return False
    if p.name.lower().startswith(("id_rsa", "id_ed25519")):
        return False
    if len(p.parts) == 1:
        return p.name in ROOT_FILES or p.suffix.lower() in SUFFIXES
    # Built-in policy and checked-in test fixtures are resources, not runtime data.
    return p.parts[0] in TREES and (
        p.suffix.lower() in SUFFIXES
        or p.name in {"Dockerfile", "Modelfile", ".gitignore"}
    )


def snapshot(source):
    return {
        "source_repo": str(source),
        "head": git(source, "rev-parse", "HEAD"),
        "branch": git(source, "branch", "--show-current"),
        "status": git(source, "status", "--porcelain=v1", "--untracked-files=all"),
    }


def inventory(sources=SOURCES, root=ROOT):
    files, excluded, repos, conflicts = [], [], {}, []
    for service, source in sources.items():
        repos[service] = snapshot(source)
        tracked = set(git(source, "ls-files", "-z").split("\0")) - {""}
        # Include ignored files for explicit exclusion evidence, never automatically allow them.
        ignored = set(
            git(
                source, "ls-files", "--others", "--ignored", "--exclude-standard", "-z"
            ).split("\0")
        ) - {""}
        untracked = set(
            git(source, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
        ) - {""}
        for relative in sorted(tracked | untracked | ignored):
            reason = None
            if relative in ignored:
                reason = "git-ignored"
            elif not allowed(relative):
                reason = "outside-allowlist-or-sensitive/generated"
            try:
                source_file = safe_path(source, relative)
                if not source_file.is_file():
                    reason = "missing-or-nonregular"
            except ValueError:
                reason = "link-or-reparse"
            if reason:
                excluded.append(
                    {"service": service, "path": relative, "reason": reason}
                )
                continue
            target_relative = f"services/{service}/{relative}"
            target = safe_path(root, target_relative)
            digest = sha(source_file)
            if target.exists() and (not target.is_file() or sha(target) != digest):
                conflicts.append(target_relative)
            files.append(
                {
                    "service": service,
                    "source_repo": str(source),
                    "head": repos[service]["head"],
                    "source_path": relative,
                    "target_path": target_relative,
                    "tracked": relative in tracked,
                    "sha256": digest,
                }
            )
    producer = next(
        f
        for f in files
        if f["target_path"]
        == "services/supportops/contracts/business_data/v1/contract.json"
    )
    for f in files.copy():
        if f["service"] == "supportops" and f["source_path"].startswith(
            "contracts/business_data/v1/"
        ):
            root_file = dict(f, target_path=f["source_path"])
            target = safe_path(root, root_file["target_path"])
            if target.exists() and (not target.is_file() or sha(target) != f["sha256"]):
                conflicts.append(root_file["target_path"])
            files.append(root_file)
    consumer = next(
        f
        for f in files
        if f["target_path"]
        == "services/datacopilot/contracts/external/business_data/v1/contract.json"
    )
    if producer["sha256"] != consumer["sha256"]:
        conflicts.append("producer/consumer contract mismatch")
    return {
        "format_version": 1,
        "repositories": repos,
        "files": files,
        "excluded": excluded,
        "conflicts": conflicts,
    }


def apply(report, root=ROOT):
    if report["conflicts"]:
        raise ValueError("conflicts: " + str(report["conflicts"]))
    manifest = safe_path(root, "docs/source-manifest.json")
    manifest_bytes = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    if manifest.exists() and manifest.read_bytes() != manifest_bytes:
        raise ValueError("existing source manifest differs; review required")
    # Preflight all hashes before any writes. Never replace an existing file.
    for item in report["files"]:
        source = safe_path(Path(item["source_repo"]), item["source_path"])
        target = safe_path(root, item["target_path"])
        if sha(source) != item["sha256"] or (
            target.exists() and sha(target) != item["sha256"]
        ):
            raise ValueError("file changed since inventory")
    for item in report["files"]:
        target = safe_path(root, item["target_path"])
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            content = safe_path(
                Path(item["source_repo"]), item["source_path"]
            ).read_bytes()
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError("source changed during copy")
            with target.open("xb") as stream:
                stream.write(content)
        if sha(target) != item["sha256"]:
            raise ValueError("target hash mismatch")
    for service, baseline in report["repositories"].items():
        if snapshot(Path(baseline["source_repo"])) != baseline:
            raise ValueError(f"source repository changed: {service}")
    if manifest.exists():
        if manifest.read_bytes() != manifest_bytes:
            raise ValueError("existing source manifest differs; review required")
    else:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open("xb") as stream:
            stream.write(manifest_bytes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = inventory()
    for item in report["files"]:
        print("COPY", item["target_path"], item["sha256"])
    for item in report["excluded"]:
        print("EXCLUDE", item["service"], item["path"], item["reason"])
    for conflict in report["conflicts"]:
        print("CONFLICT", conflict)
    if report["conflicts"]:
        return 1
    if args.apply:
        apply(report)
    print(f"PASS: {len(report['files'])} files; {len(report['excluded'])} excluded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
