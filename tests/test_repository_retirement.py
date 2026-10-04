"""Retired sources must not be needed, and local provenance must remain strict."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify_import
from import_sources import sha


def setup_manifest(tmp_path):
    local = tmp_path / "services/support/app/example.py"
    local.parent.mkdir(parents=True)
    local.write_text("original bytes", encoding="utf-8")
    (tmp_path / "services/support/LICENSE").write_text("license", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "source-manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "service": "supportops",
                        "source_repo": str(tmp_path / "deleted-original"),
                        "source_path": "app/example.py",
                        "target_path": "services/supportops/app/example.py",
                        "sha256": sha(local),
                    }
                ],
                "repositories": {
                    "supportops": {"source_repo": str(tmp_path / "deleted-original")}
                },
            }
        ),
        encoding="utf-8",
    )
    return local


def test_local_provenance_with_deleted_originals(tmp_path, monkeypatch):
    setup_manifest(tmp_path)
    monkeypatch.setattr(verify_import, "ROOT", tmp_path)
    monkeypatch.setattr(
        verify_import, "snapshot", lambda _: pytest.fail("original read")
    )
    verify_import.verify()
    report = json.loads((tmp_path / ".runtime/import-audit.json").read_text())
    assert report["passed"] and report["verified_files"] == 1
    assert report["source_git_unchanged"] is None
    assert report["original_repositories_checked"] is False


def test_local_drift_still_fails(tmp_path, monkeypatch):
    local = setup_manifest(tmp_path)
    monkeypatch.setattr(verify_import, "ROOT", tmp_path)
    local.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_import.verify()


def test_explicit_original_audit_does_not_silently_fallback(tmp_path, monkeypatch):
    setup_manifest(tmp_path)
    monkeypatch.setattr(verify_import, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        verify_import.verify(verify_originals=True)
