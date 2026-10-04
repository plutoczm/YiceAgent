"""A layout migration must retain real Git source bytes and fail on false evidence."""

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "scripts/maintenance/verify_layout.py"
spec = importlib.util.spec_from_file_location("layout_history", SOURCE)
assert spec and spec.loader
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)


def snapshot(tmp_path):
    def git(*args):
        return subprocess.check_output(
            [
                "git",
                "-c",
                "user.name=Layout Test",
                "-c",
                "user.email=layout@example.invalid",
                *args,
            ],
            cwd=tmp_path,
            text=True,
        ).strip()

    git("init", "-q")
    (tmp_path / ".gitattributes").write_text("* -text\n")
    original = b"original\r\nsource\r\n"
    (tmp_path / "source.py").write_bytes(original)
    git("add", ".gitattributes", "source.py")
    git("commit", "-qm", "test source snapshot")
    commit = git("rev-parse", "HEAD")
    (tmp_path / "source.py").unlink()
    target = tmp_path / "src/current.py"
    target.parent.mkdir()
    target.write_bytes(b"current\nsource\n")
    record = {
        "source_commit": commit,
        "entries": [
            {
                "before": "source.py",
                "before_sha256": hashlib.sha256(original).hexdigest(),
                "after": "src/current.py",
                "after_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        ],
    }
    manifest = tmp_path / ".planning/directory-normalization/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(record))
    return manifest, record, target


def test_real_git_archive_survives_source_removal_and_checks_output(tmp_path):
    _, _, target = snapshot(tmp_path)
    assert history.verify(tmp_path, strict=True)["historical_files_verified"] == 1
    target.write_text("unreviewed replacement")
    with pytest.raises(ValueError, match="current_layout_hash_mismatch"):
        history.verify(tmp_path, strict=True)
    assert history.verify(tmp_path)["result"] == "PASS"


def test_false_original_hash_cannot_claim_source_preservation(tmp_path):
    manifest, record, _ = snapshot(tmp_path)
    record["entries"][0]["before_sha256"] = "0" * 64
    manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="historical_git_hash_mismatch"):
        history.verify(tmp_path)


def test_manifest_cannot_escape_the_workspace(tmp_path):
    manifest, record, _ = snapshot(tmp_path)
    record["entries"][0]["after"] = "../external.py"
    manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="invalid_manifest_path"):
        history.verify(tmp_path, strict=True)
