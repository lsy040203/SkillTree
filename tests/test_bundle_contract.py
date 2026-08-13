from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"
sys.path.insert(0, str(ROOT / "src"))

from skilltree.bundle import build_bundle, validate_bundle


class BundleContractTests(unittest.TestCase):
    def test_p0_bundle_has_a_complete_verified_offline_manifest(self) -> None:
        manifest = validate_bundle(PLUGIN_ROOT)

        self.assertEqual(manifest["schema_version"], "skilltree-bundle/v1")
        self.assertEqual(manifest["plugin"]["name"], "skilltree")
        self.assertEqual(manifest["plugin"]["version"], "0.1.0")
        self.assertEqual(manifest["core"]["distribution"], "skilltree-core")
        self.assertEqual(manifest["core"]["version"], "0.1.0")
        self.assertEqual(manifest["schema"], {"version": "skilltree/v1", "migration_version": 1})
        self.assertEqual(
            manifest["migrations"],
            [
                {
                    "version": 1,
                    "path": "migrations/0001_p0_runtime.sql",
                    "sha256": manifest["migrations"][0]["sha256"],
                }
            ],
        )
        self.assertTrue((PLUGIN_ROOT / "runtime" / "wheels" / "skilltree_core-0.1.0-py3-none-any.whl").is_file())
        self.assertFalse(list(PLUGIN_ROOT.rglob("*.tar.gz")))
        self.assertFalse(list(PLUGIN_ROOT.rglob("*.oci.tar")))

    def test_manifest_hashes_match_the_files_it_declares(self) -> None:
        manifest = validate_bundle(PLUGIN_ROOT)

        entries = [
            manifest["plugin"],
            manifest["core"],
            manifest["requirements_lock"],
            *manifest["migrations"],
            *manifest["runtime_files"],
        ]
        for entry in entries:
            relative_path = entry.get("path") or entry.get("manifest_path") or entry.get("wheel")
            self.assertEqual(entry["sha256"], _sha256(PLUGIN_ROOT / relative_path))

        self.assertEqual(manifest["bundle_hash"], _bundle_hash(manifest))

    def test_p0_migration_contains_only_p0_tables(self) -> None:
        sql = (PLUGIN_ROOT / "migrations" / "0001_p0_runtime.sql").read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE schema_migrations", sql)
        self.assertIn("CREATE TABLE runtime_config", sql)
        self.assertIn("CREATE TABLE audit_events", sql)
        self.assertNotIn("runtime_settings", sql)
        self.assertNotIn("hook_observations", sql)

    def test_p0_hook_does_not_read_the_retired_config_file(self) -> None:
        hook_source = (PLUGIN_ROOT / "runtime" / "skilltree_hook.py").read_text(encoding="utf-8")

        self.assertNotIn("config.json", hook_source)
        self.assertNotIn("sys.stdin", hook_source)

    def test_validator_rejects_a_tampered_migration_before_installation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            copied_plugin = Path(temp_dir) / "skilltree"
            _copy_plugin_tree(PLUGIN_ROOT, copied_plugin)
            migration = copied_plugin / "migrations" / "0001_p0_runtime.sql"
            migration.write_text(migration.read_text(encoding="utf-8") + "\n-- tampered\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                validate_bundle(copied_plugin)

    def test_repeated_builds_produce_the_same_bundle_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            copied_root = Path(temp_dir) / "repository"
            _copy_repository_for_build(ROOT, copied_root)

            first = build_bundle(copied_root)
            second = build_bundle(copied_root)

        self.assertEqual(first["bundle_hash"], second["bundle_hash"])
        self.assertEqual(first["core"]["sha256"], second["core"]["sha256"])

    def test_core_wheel_build_does_not_invoke_pip_or_a_pep517_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            copied_root = Path(temp_dir) / "repository"
            _copy_repository_for_build(ROOT, copied_root)

            manifest = build_bundle(copied_root)
            wheel_path = copied_root / "plugins" / "skilltree" / manifest["core"]["wheel"]
            with zipfile.ZipFile(wheel_path) as wheel:
                names = set(wheel.namelist())
                metadata = wheel.read("skilltree_core-0.1.0.dist-info/METADATA").decode("utf-8")
                wheel_metadata = wheel.read("skilltree_core-0.1.0.dist-info/WHEEL").decode("utf-8")

        self.assertIn("skilltree/bundle.py", names)
        self.assertIn("skilltree_core-0.1.0.dist-info/RECORD", names)
        self.assertIn("Name: skilltree-core", metadata)
        self.assertIn("Tag: py3-none-any", wheel_metadata)
        self.assertNotIn("pip", (ROOT / "src" / "skilltree" / "bundle.py").read_text(encoding="utf-8"))
        self.assertNotIn("setuptools", (ROOT / "src" / "skilltree" / "bundle.py").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.environ.get("SKILLTREE_UV_PYTHON"), "requires an explicit uv Python path")
    def test_uv_python_without_setuptools_can_build_the_bundle(self) -> None:
        uv_python = os.environ["SKILLTREE_UV_PYTHON"]
        with tempfile.TemporaryDirectory() as temp_dir:
            copied_root = Path(temp_dir) / "repository"
            _copy_repository_for_build(ROOT, copied_root)

            completed = subprocess.run(
                [uv_python, "tools/build_bundle.py"],
                cwd=copied_root,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertRegex(completed.stdout.strip(), r"^sha256:[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle_hash(manifest: dict[str, object]) -> str:
    unsigned = dict(manifest)
    unsigned.pop("bundle_hash")
    canonical = json.dumps(unsigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _copy_plugin_tree(source: Path, destination: Path) -> None:
    for path in source.rglob("*"):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


def _copy_repository_for_build(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(".git", ".venv", "build", "__pycache__", "*.pyc"),
    )
