"""Scoped, reversible source and environment relocation for the approved rename."""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

OLD = Path("D:/Projects/WorkflowOps")
NEW = Path("D:/Projects/YiceAgent")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def replace_root(text):
    for old, new in (
        (str(OLD), str(NEW)),
        (OLD.as_posix(), NEW.as_posix()),
        (str(OLD).replace("\\", "\\\\"), str(NEW).replace("\\", "\\\\")),
    ):
        text = text.replace(old, new)
    return text


def backup(root, path):
    dest = root / ".runtime/v2/project-rename/backups" / path.relative_to(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(path.read_bytes())


def baseline():
    out = OLD / ".runtime/v2/project-rename"
    out.mkdir(exist_ok=True)
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=OLD, capture_output=True, check=True,
    )
    paths = sorted(set(x.decode("utf-8") for x in result.stdout.split(b"\0") if x))
    hashes = {}
    with zipfile.ZipFile(out / "pre-rename-source.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for relative in paths:
            p = OLD / relative
            if p.is_file():
                data = p.read_bytes()
                hashes[relative] = digest(data)
                archive.writestr(relative, data)
    protected = {}
    for p in (
        OLD / ".runtime/v2/settings.json",
        OLD / ".runtime/v2/chatgpt-oauth.json",
        OLD / ".runtime/v2/models/manifest.json",
        OLD / "docs/validation/acceptance-manifest.json",
    ):
        protected[p.relative_to(OLD).as_posix()] = digest(p.read_bytes())
    git = {
        name: subprocess.run(args, cwd=OLD, capture_output=True, check=True).stdout.decode("utf-8")
        for name, args in {
            "head": ["git", "rev-parse", "HEAD"],
            "refs": ["git", "show-ref"],
            "status": ["git", "status", "--short", "--branch"],
        }.items()
    }
    report = {"old_root": str(OLD), "new_root": str(NEW), "files": hashes, "protected": protected, "git": git}
    (out / "baseline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"baseline_files": len(hashes), "protected": list(protected)}))


def edit():
    root = OLD
    files = []
    for directory in ("src", "scripts", "tests"):
        files.extend(p for p in (root / directory).rglob("*") if p.is_file() and p.suffix in {".py", ".js", ".mjs", ".html", ".css"})
    files += [root / "pyproject.toml", root / "README.md", root / "AGENTS.md"]
    files += [p for p in (root / "docs").glob("*.md") if p.name not in {"PLAN.md", "VERSIONING.md"}]
    changed = []
    for p in files:
        data = p.read_bytes()
        text = data.decode("utf-8")
        new = replace_root(text)
        new = new.replace("src/workflowops", "src/yice_agent")
        new = re.sub(r"\b(from|import) workflowops\b", r"\1 yice_agent", new)
        # Cover subprocess modules and monkeypatch targets, while preserving Docker labels.
        new = re.sub(r"(?<!com\.)\bworkflowops\.", "yice_agent.", new)
        new = new.replace("workflowops-react-v1", "yice-agent-react-v1")
        new = new.replace("X-WorkflowOps", "X-Yice-Agent")
        new = new.replace("WORKFLOWOPS_V2_", "YICE_AGENT_")
        new = new.replace("workflowops_v2_session", "yice_agent_session")
        new = new.replace("workflowops:", "yice-agent:")
        # Active UI/product text, never frozen corpus or provenance.
        new = new.replace("WorkflowOps v2", "忆策 Agent")
        new = new.replace("WorkflowOps", "忆策 Agent")
        if p.name == "pyproject.toml":
            new = new.replace('name = "workflowops-v2"', 'name = "yice-agent"')
        if p.name == "AGENTS.md":
            new = new.replace("unique workflowops namespace", "unique yice_agent namespace")
        if p.name == "STRUCTURE.md":
            new = new.replace("保持 workflowops 命名空间", "保持 yice_agent 命名空间")
        if p.name == "README.md":
            new = new.replace("忆策 Agent/", "YiceAgent/")
        if p.suffix == ".html":
            new = new.replace('aria-hidden="true">W</span>', 'aria-hidden="true">忆</span>')
            new = new.replace("WORKFLOWOPS", "YICE AGENT")
        encoded = new.encode("utf-8")
        if encoded != data:
            backup(root, p)
            p.write_bytes(encoded)
            changed.append(p.relative_to(root).as_posix())
    # Preserve the pre-rename browser selection under a read-only fallback key.
    agent_js = root / "src/workflowops/static/agent.js"
    text = agent_js.read_text(encoding="utf-8")
    text = text.replace(
        'const saved=localStorage.getItem("yice_agent.conversation."+actorPersona);',
        'const saved=localStorage.getItem("yice_agent.conversation."+actorPersona) || localStorage.getItem("workflowops.conversation."+actorPersona);',
    )
    agent_js.write_text(text, encoding="utf-8", newline="\n")
    for relative in ("bridge/package.json", "bridge/package-lock.json", "bridge/node_modules/.package-lock.json"):
        p = root / relative
        if p.is_file():
            data = p.read_bytes()
            new = data.replace(b"workflowops-pi-bridge", b"yice-agent-pi-bridge")
            if new != data:
                backup(root, p)
                p.write_bytes(new)
                changed.append(relative)
    compose = root / "infra/compose.yml"
    backup(root, compose)
    compose.write_bytes(compose.read_bytes().replace(b"WORKFLOWOPS_V2_", b"YICE_AGENT_"))
    changed.append("infra/compose.yml")
    # Update current Plan heading/location; dated entries describe their original state.
    p = root / "docs/PLAN.md"
    backup(root, p)
    text = p.read_text(encoding="utf-8").replace("# WorkflowOps v2 实施计划", "# 忆策 Agent 实施计划")
    text = text.replace("v2 代码使用 src/workflowops 命名空间，工作根目录仍为 WorkflowOps。", "当前代码使用 src/yice_agent 命名空间，工作根目录为 D:/Projects/YiceAgent。")
    p.write_text(text, encoding="utf-8", newline="\n")
    (root / "src/workflowops").rename(root / "src/yice_agent")
    (root / ".runtime/v2/project-rename/source-edits.json").write_text(json.dumps(changed, indent=2), encoding="utf-8")
    print(json.dumps({"source_files_updated": len(changed), "namespace": "yice_agent"}))


def relocate():
    assert OLD.parent.resolve() == NEW.parent.resolve() == Path("D:/Projects").resolve()
    assert OLD.resolve() == OLD and not OLD.is_symlink()
    assert (OLD / ".git").is_dir() and (OLD / "src/yice_agent").is_dir()
    if NEW.exists():
        raise RuntimeError("rename_target_already_exists")
    if (OLD / ".runtime/v2/app-process.json").exists():
        raise RuntimeError("owned_application_still_running")
    os.chdir(OLD.parent)
    try:
        # The user selected this fallback after Windows denied the root rename.
        raise PermissionError(32, "approved_shared_git_relocation")
    except PermissionError:
        # Open Codex/REPL cwd handles pin only the root directory. Move its contents
        # on the same volume without touching or interrupting those applications.
        NEW.mkdir()
        moved = []
        created = []
        pinned = []

        def move_entry(source, destination):
            try:
                source.rename(destination)
                moved.append((source, destination))
            except PermissionError:
                if not source.is_dir() or source.is_symlink():
                    raise
                # A directory watcher can pin the container but allow its contents
                # to move. Empty pinned directories are explicitly reported.
                destination.mkdir()
                created.append(destination)
                for entry in source.iterdir():
                    move_entry(entry, destination / entry.name)
                try:
                    source.rmdir()
                except PermissionError:
                    assert not any(source.iterdir())
                    pinned.append(str(source))

        try:
            for child in OLD.iterdir():
                if child.name == ".git":
                    continue  # User approved one shared Git directory until the old UI releases it.
                assert child.parent.resolve() == OLD.resolve()
                destination = NEW / child.name
                assert destination.parent.resolve() == NEW.resolve() and not destination.exists()
                move_entry(child, destination)
        except BaseException:
            for source, destination in reversed(moved):
                source.parent.mkdir(parents=True, exist_ok=True)
                destination.rename(source)
            for directory in reversed(created):
                directory.rmdir()
            NEW.rmdir()
            raise
        method = "same_volume_content_move_shared_git_metadata"
        (NEW / ".git").write_text("gitdir: " + (OLD / ".git").as_posix() + "\n", encoding="utf-8")
        subprocess.run(["git", "--git-dir=" + str(OLD / ".git"), "config", "core.worktree", str(NEW)], check=True)
        assert not [p for p in OLD.rglob("*") if ".git" not in p.relative_to(OLD).parts and p.is_file()]
    record = {"relocated": str(NEW), "method": method, "old_root_exists": OLD.exists(), "old_root_empty": not OLD.exists() or not any(OLD.iterdir()), "git_metadata_pending": (NEW / ".git").is_file(), "pinned_empty_directories": pinned}
    (NEW / ".runtime/v2/project-rename/relocation.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record))


def repair():
    root = NEW
    rows = []
    for environment in ("main", "inference"):
        env = root / ".runtime/v2/envs" / environment
        candidates = [env / "pyvenv.cfg"]
        candidates += [p for p in (env / "Scripts").iterdir() if p.is_file() and p.suffix != ".exe"]
        candidates += list((env / "Lib/site-packages").glob("*.pth"))
        candidates += list((env / "Lib/site-packages").glob("*.dist-info/direct_url.json"))
        for p in candidates:
            data = p.read_bytes()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            new = replace_root(text).encode("utf-8")
            if new != data:
                backup(root, p)
                p.write_bytes(new)
                rows.append(p.relative_to(root).as_posix())
        # Distlib launchers consist of unchanged PE + interpreter shebang + appended zip.
        # Rebuild the interpreter header only; zipfile accepts the changed prefix offset.
        for p in (env / "Scripts").glob("*.exe"):
            data = p.read_bytes()
            old_python = str(OLD / ".runtime/v2/envs" / environment / "Scripts/python.exe").encode()
            if old_python not in data:
                continue
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                script = archive.read("__main__.py")
            start = data.find(b"#!" + old_python)
            if start == -1:
                start = data.find(b'#!"' + old_python + b'"')
            if start == -1 or data.count(old_python) != 1:
                raise RuntimeError("unexpected_console_launcher")
            end = data.index(b"\n", start) + 1
            shebang = b"#!" + str(env / "Scripts/python.exe").encode() + b"\n"
            rewritten = data[:start] + shebang + data[end:]
            with zipfile.ZipFile(io.BytesIO(rewritten)) as archive:
                assert archive.read("__main__.py") == script
            backup(root, p)
            p.write_bytes(rewritten)
            rows.append(p.relative_to(root).as_posix())
    site = root / ".runtime/v2/envs/main/Lib/site-packages"
    old_meta = site / "workflowops_v2-0.1.0.dist-info"
    new_meta = site / "yice_agent-0.1.0.dist-info"
    old_pth = site / "_editable_impl_workflowops_v2.pth"
    new_pth = site / "_editable_impl_yice_agent.pth"
    if old_meta.exists():
        if new_meta.exists() or new_pth.exists():
            raise RuntimeError("editable_metadata_name_conflict")
        backup(root, old_meta / "METADATA")
        (old_meta / "METADATA").write_bytes((old_meta / "METADATA").read_bytes().replace(b"Name: workflowops-v2", b"Name: yice-agent"))
        backup(root, old_meta / "RECORD")
        record = list(csv.reader(io.StringIO((old_meta / "RECORD").read_text(encoding="utf-8"))))
        for row in record:
            row[0] = row[0].replace("workflowops_v2-0.1.0.dist-info", "yice_agent-0.1.0.dist-info").replace("_editable_impl_workflowops_v2.pth", "_editable_impl_yice_agent.pth")
            row[1:] = ["", ""]  # Paths remain correct; modified metadata has no stale hashes.
        target = io.StringIO(newline="")
        csv.writer(target).writerows(record)
        (old_meta / "RECORD").write_text(target.getvalue(), encoding="utf-8", newline="")
        old_meta.rename(new_meta)
        old_pth.rename(new_pth)
        rows += [new_meta.relative_to(root).as_posix(), new_pth.relative_to(root).as_posix()]
    p = root / ".runtime/v2/models/manifest.json"
    before = json.loads(p.read_text(encoding="utf-8"))
    after = json.loads(json.dumps(before))
    for model in after["models"]:
        model["snapshot"] = replace_root(model["snapshot"])
        assert Path(model["snapshot"]).is_relative_to(root / ".runtime/v2/models")
        assert Path(model["snapshot"]).is_dir()
    backup(root, p)
    p.write_text(json.dumps(after, indent=2) + "\n", encoding="utf-8")
    rows.append(p.relative_to(root).as_posix())
    (root / ".runtime/v2/project-rename/environment-repairs.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps({"owned_environment_paths_repaired": len(rows)}))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["baseline", "edit", "relocate", "repair"])
    mode = p.parse_args().mode
    {"baseline": baseline, "edit": edit, "relocate": relocate, "repair": repair}[mode]()
