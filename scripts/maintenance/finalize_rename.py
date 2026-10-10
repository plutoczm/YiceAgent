"""Finish the approved YiceAgent rename after the old Codex project releases Git."""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT.parent / "WorkflowOps"


def git(*args, directory=ROOT):
    return subprocess.run(
        ["git", *args], cwd=directory, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.strip()


def finish(approved=False):
    if ROOT != Path("D:/Projects/YiceAgent"):
        raise ValueError("unexpected_project_root")
    pointer = ROOT / ".git"
    if pointer.is_dir():
        return {"status": "COMPLETE", "git_directory": str(pointer), "old_directory": OLD.exists()}
    expected = "gitdir: " + (OLD / ".git").as_posix()
    if not pointer.is_file() or pointer.read_text(encoding="utf-8").strip() != expected:
        raise ValueError("unexpected_git_pointer")
    if git("rev-parse", "--show-toplevel").replace("\\", "/") != ROOT.as_posix():
        raise ValueError("unexpected_git_worktree")
    metadata = OLD / ".git"
    if not metadata.is_dir() or metadata.is_symlink():
        raise ValueError("unexpected_git_directory")
    if list(metadata.glob("*.lock")):
        raise ValueError("git_operation_in_progress")
    if not approved:
        return {"status": "PENDING", "project_directory": str(ROOT), "git_directory": str(metadata)}
    head, refs = git("rev-parse", "HEAD"), git("show-ref")
    temporary = ROOT / ".runtime/v2/project-rename/gitdir-pointer.backup"
    if temporary.exists():
        raise ValueError("rename_backup_already_exists")
    pointer.rename(temporary)
    try:
        metadata.rename(pointer)
    except PermissionError:
        temporary.rename(pointer)
        return {
            "status": "PENDING",
            "reason": "old_git_directory_still_in_use",
            "next": "Close the old Codex project, then run this command from a separate terminal.",
        }
    except BaseException:
        temporary.rename(pointer)
        raise
    git("config", "--unset", "core.worktree")
    assert git("rev-parse", "HEAD") == head and git("show-ref") == refs
    assert git("rev-parse", "--show-toplevel").replace("\\", "/") == ROOT.as_posix()
    if OLD.exists() and not any(OLD.iterdir()):
        try:
            OLD.rmdir()  # Verified empty directory only; no recursive cleanup.
        except PermissionError:
            pass
    report = {
        "status": "COMPLETE",
        "git_directory": str(pointer),
        "old_empty_directory_pending": OLD.exists(),
    }
    (ROOT / ".runtime/v2/project-rename/git-finalization.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--approved", action="store_true", help="Finish the user-approved metadata move")
    a = p.parse_args()
    print(json.dumps(finish(a.approved), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
