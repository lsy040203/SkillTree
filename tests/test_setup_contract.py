from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"
UV_PYTHON = os.environ.get(
    "SKILLTREE_UV_PYTHON",
    r"C:\Users\Lenovo\AppData\Roaming\uv\python\cpython-3.14.6-windows-x86_64-none\python.exe",
)


@unittest.skipUnless(Path(UV_PYTHON).is_file(), "requires the configured bootstrap Python")
class SetupContractTests(unittest.TestCase):
    def _run_setup(self, data_dir: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(PLUGIN_ROOT / "scripts" / "setup.ps1"), "-PluginData", str(data_dir),
                "-PythonPath", UV_PYTHON,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_bootstrap_rejects_an_invalid_control_message_without_creating_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            environment = {
                **os.environ,
                "PLUGIN_ROOT": str(PLUGIN_ROOT),
                "PLUGIN_DATA": str(data_dir),
            }
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(PLUGIN_ROOT / "runtime" / "skilltree_bootstrap.ps1"),
                ],
                input=json.dumps({"prompt": "$skilltree-bootstrap install --python relative\\python.exe"}),
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"decision": "block", "reason": "skilltree_bootstrap_failed:invalid_bootstrap_request"},
        )
        self.assertFalse(data_dir.exists())

    def test_standalone_bundle_validator_rejects_a_tampered_hook_bundle_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            copied_plugin = Path(temp_dir) / "skilltree"
            shutil.copytree(PLUGIN_ROOT, copied_plugin)
            hooks_path = copied_plugin / "hooks" / "hooks.json"
            hooks_path.write_text(hooks_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            result = subprocess.run(
                [UV_PYTHON, "-I", str(copied_plugin / "runtime" / "bundle_validate.py"), str(copied_plugin)],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 3)

    def test_standalone_bundle_validator_rejects_an_extra_runtime_wheel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            copied_plugin = Path(temp_dir) / "skilltree"
            shutil.copytree(PLUGIN_ROOT, copied_plugin)
            (copied_plugin / "runtime" / "wheels" / "unexpected-0.0.0-py3-none-any.whl").write_bytes(b"not a wheel")
            result = subprocess.run(
                [UV_PYTHON, "-I", str(copied_plugin / "runtime" / "bundle_validate.py"), str(copied_plugin)],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 3)

    def test_setup_creates_an_offline_runtime_and_applies_the_p3_1_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            result = self._run_setup(data_dir)
            state_path = data_dir / "runtime-state.json"
            with closing(sqlite3.connect(data_dir / "skilltree.sqlite3")) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "installed")
        self.assertEqual(tables, {
            "schema_migrations", "runtime_config", "audit_events", "skills", "run_contexts", "route_offers",
            "route_decisions", "turn_traces", "run_turn_bindings", "trace_events", "hook_observations",
            "outcome_assessments", "episodes", "skill_weights", "skill_weight_updates",
            "memory_write_breakers", "memory_candidates", "profile_fields", "procedures",
        })
        self.assertEqual(state["schema_version"], "skilltree-runtime/v1")

    def test_setup_publishes_a_working_cli_launcher_after_staging_venv_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            result = self._run_setup(data_dir)
            launcher = data_dir / "venv" / "Scripts" / "skilltree.exe"
            shim = data_dir / "bin" / "skilltree.cmd"
            cli_shim = data_dir / "bin" / "skilltree-cli.cmd"
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(launcher.is_file())
            self.assertTrue(shim.is_file())
            self.assertTrue(cli_shim.is_file())
            direct = subprocess.run([str(launcher), "--help"], text=True, capture_output=True, check=False)
            wrapped = subprocess.run(["cmd.exe", "/d", "/c", str(shim), "--help"], text=True, capture_output=True, check=False)

        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertIn("usage: skilltree", direct.stdout)
        self.assertEqual(wrapped.returncode, 0, wrapped.stderr)
        self.assertIn("usage: skilltree", wrapped.stdout)

    def test_setup_returns_already_installed_for_a_verified_matching_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            first = self._run_setup(data_dir)
            second = self._run_setup(data_dir)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)["status"], "already_installed")

    def test_bootstrap_reports_already_installed_after_a_matching_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            setup = self._run_setup(data_dir)
            environment = {
                **os.environ,
                "PLUGIN_ROOT": str(PLUGIN_ROOT),
                "PLUGIN_DATA": str(data_dir),
            }
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(PLUGIN_ROOT / "runtime" / "skilltree_bootstrap.ps1"),
                ],
                input=json.dumps({"prompt": f'$skilltree-bootstrap install --python "{UV_PYTHON}"'}),
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"decision": "block", "reason": "skilltree_bootstrap_already_installed"},
        )

    def test_setup_rejects_a_plugin_data_directory_inside_the_workspace(self) -> None:
        data_dir = ROOT / ".temporary-plugin-data"
        result = self._run_setup(data_dir)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "invalid_argument")
        self.assertFalse(data_dir.exists())

    def test_setup_rejects_a_database_schema_newer_than_the_bundle_without_writing_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            data_dir.mkdir()
            database_path = data_dir / "skilltree.sqlite3"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, content_hash TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at, content_hash) VALUES (2, '2026-01-01T00:00:00Z', 'sha256:test')"
                )
                connection.commit()

            result = self._run_setup(data_dir)
            state_exists = (data_dir / "runtime-state.json").exists()
            with closing(sqlite3.connect(database_path)) as connection:
                versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations")]

        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "database_initialize_failed")
        self.assertFalse(state_exists)
        self.assertEqual(versions, [2])

    def test_setup_rejects_a_missing_core_wheel_without_writing_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            copied_plugin = Path(temp_dir) / "skilltree"
            shutil.copytree(PLUGIN_ROOT, copied_plugin)
            next((copied_plugin / "runtime" / "wheels").glob("*.whl")).unlink()
            data_dir = Path(temp_dir) / "plugin-data"
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(copied_plugin / "scripts" / "setup.ps1"), "-PluginData", str(data_dir),
                    "-PythonPath", UV_PYTHON,
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            state_exists = (data_dir / "runtime-state.json").exists()

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "bundle_validation_failed")
        self.assertFalse(state_exists)

    def test_setup_does_not_modify_an_existing_runtime_when_bundle_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            installed = self._run_setup(data_dir)
            state_path = data_dir / "runtime-state.json"
            original_state = state_path.read_bytes()
            original_python = data_dir / "venv" / "Scripts" / "python.exe"
            copied_plugin = Path(temp_dir) / "skilltree"
            shutil.copytree(PLUGIN_ROOT, copied_plugin)
            next((copied_plugin / "runtime" / "wheels").glob("*.whl")).unlink()
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(copied_plugin / "scripts" / "setup.ps1"), "-PluginData", str(data_dir),
                    "-PythonPath", UV_PYTHON,
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            state_after = state_path.read_bytes()
            python_still_present = original_python.exists()

        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(state_after, original_state)
        self.assertTrue(python_still_present)

    def test_setup_restores_the_existing_runtime_when_state_switch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            installed = self._run_setup(data_dir)
            state_path = data_dir / "runtime-state.json"
            original_python = data_dir / "venv" / "Scripts" / "python.exe"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["bundle_hash"] = "sha256:" + "0" * 64
            state_path.write_text(json.dumps(state), encoding="utf-8")
            incoming_state = state_path.read_bytes()
            state_temp_path = data_dir / "runtime-state.json.tmp"
            state_temp_path.mkdir()

            result = self._run_setup(data_dir)
            state_after = state_path.read_bytes()
            python_still_present = original_python.exists()

        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "rolled_back")
        self.assertEqual(state_after, incoming_state)
        self.assertTrue(python_still_present)
