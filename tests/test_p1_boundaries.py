"""Adversarial checks for the import and process boundaries introduced by P1."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


imports = load("import_sources")
dev = load("dev")
contracts = load("check_contracts")


class Boundaries(unittest.TestCase):
    def test_manifest_records_json_and_apply_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "a.py").write_bytes(b"print('source')")
            target = root / "target"
            target.mkdir()
            report = {
                "conflicts": [],
                "repositories": {},
                "files": [
                    {
                        "source_repo": str(source),
                        "source_path": "a.py",
                        "target_path": "a.py",
                        "sha256": imports.sha(source / "a.py"),
                    }
                ],
            }
            imports.apply(report, target)
            self.assertEqual(
                json.loads((target / "docs/source-manifest.json").read_text()), report
            )
            imports.apply(report, target)
            self.assertEqual((target / "a.py").read_bytes(), b"print('source')")

    def test_secret_and_generated_files_rejected(self):
        for path in [
            ".env",
            "docker/.env.production",
            "app/private.key",
            "app/supportops.db",
            "models/config.json",
            "supportops_ai.egg-info/PKG-INFO",
            "app/__pycache__/a.py",
            ".tools/run.py",
            "data/download.json",
        ]:
            self.assertFalse(imports.allowed(path), path)
        for path in [
            "backend/src/app/db/models/tenant.py",
            "app/data/knowledge_sources.json",
            "app/business_data/export_v1.py",
            "tests/fixtures/business_data/delivery_v2/orders_v1.jsonl",
        ]:
            self.assertTrue(imports.allowed(path), path)

    def test_conflict_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "a.py").write_bytes(b"new")
            target = root / "target"
            target.mkdir()
            (target / "a.py").write_bytes(b"user work")
            report = {
                "conflicts": [],
                "repositories": {},
                "files": [
                    {
                        "source_repo": str(source),
                        "source_path": "a.py",
                        "target_path": "a.py",
                        "sha256": imports.sha(source / "a.py"),
                    }
                ],
            }
            with self.assertRaises(ValueError):
                imports.apply(report, target)
            self.assertEqual((target / "a.py").read_bytes(), b"user work")

    def test_source_drift_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source.py").write_bytes(b"changed")
            report = {
                "conflicts": [],
                "repositories": {},
                "files": [
                    {
                        "source_repo": str(root),
                        "source_path": "source.py",
                        "target_path": "target.py",
                        "sha256": "wrong",
                    }
                ],
            }
            with self.assertRaises(ValueError):
                imports.apply(report, root)
            self.assertFalse((root / "target.py").exists())

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            imports.safe_path(ROOT, "../outside.py")

    def test_reparse_point_rejected(self):
        with patch.object(Path, "lstat") as mocked:
            mocked.return_value.st_mode = 0
            mocked.return_value.st_file_attributes = 1024
            with self.assertRaises(ValueError):
                imports.safe_path(ROOT, "scripts/dev.py")

    def test_contract_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [
                "contracts/business_data/v1/contract.json",
                "services/supportops/contracts/business_data/v1/contract.json",
                "services/datacopilot/contracts/external/business_data/v1/contract.json",
            ]
            for relative in paths:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"{}")
            acceptance = (
                root
                / "services/datacopilot/contracts/external/business_data/v1/acceptance.json"
            )
            acceptance.write_text(
                json.dumps({"contract_sha256": imports.sha(root / paths[0])})
            )
            contracts.check(root)
            (root / paths[1]).write_bytes(b"changed")
            with self.assertRaises(ValueError):
                contracts.check(root)
            (root / paths[1]).write_bytes(b"{}")
            acceptance.write_text(json.dumps({"contract_sha256": "changed"}))
            with self.assertRaises(ValueError):
                contracts.check(root)

    def test_each_dispatch_replaces_inherited_pythonpath(self):
        with (
            patch.object(dev.subprocess, "run") as run,
            patch.dict(dev.os.environ, {"PYTHONPATH": "old-source"}),
        ):
            for service, (environment, source_path, _, _, _) in dev.SERVICES.items():
                dev.service_process(service, ["-m", "pytest"])
                args, kwargs = run.call_args
                self.assertIn(environment, args[0])
                self.assertEqual(kwargs["cwd"], ROOT / "services" / service)
                self.assertEqual(
                    kwargs["env"]["PYTHONPATH"],
                    str((kwargs["cwd"] / source_path).resolve()),
                )


if __name__ == "__main__":
    unittest.main()
