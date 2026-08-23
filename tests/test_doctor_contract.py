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
class DoctorContractTests(unittest.TestCase):
    def _setup(self, data_dir: Path) -> subprocess.CompletedProcess[str]:
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

    def _doctor(self, data_dir: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(data_dir / "venv" / "Scripts" / "python.exe"), "-B", "-I", "-m", "skilltree", "doctor", "--data-dir", str(data_dir), "--json"],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_p0_runtime_is_degraded_only_because_hook_observation_is_unconfirmed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            setup = self._setup(data_dir)
            doctor = self._doctor(data_dir)

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(doctor.returncode, 1, doctor.stderr)
        payload = json.loads(doctor.stdout)
        self.assertEqual(payload["schema_version"], "skilltree-doctor/v1")
        self.assertTrue(payload["runtime_ready"])
        self.assertEqual(payload["diagnostic_state"], "degraded")
        self.assertEqual([check["name"] for check in payload["checks"]], [
            "runtime_state", "venv_python", "bundle_manifest", "versions",
            "schema_migrations", "hook_bundle", "hook_observation",
        ])
        self.assertEqual(payload["checks"][-1], {"name": "hook_observation", "state": "unknown", "code": "hook_unconfirmed"})

    def test_doctor_reports_observed_when_current_hook_hash_has_a_persisted_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            setup = self._setup(data_dir)
            state = json.loads((data_dir / "runtime-state.json").read_text(encoding="utf-8"))
            with closing(sqlite3.connect(data_dir / "skilltree.sqlite3")) as connection:
                connection.execute(
                    "INSERT INTO hook_observations "
                    "(hook_bundle_hash, first_observed_at, last_observed_at, observed_count, last_event_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (state["hook_bundle_hash"], "2026-08-21T11:08:00Z", "2026-08-21T11:09:00Z", 1, "event-1"),
                )
                connection.commit()
            doctor = self._doctor(data_dir)

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        payload = json.loads(doctor.stdout)
        self.assertTrue(payload["runtime_ready"])
        self.assertEqual(payload["diagnostic_state"], "ready")
        self.assertEqual(payload["hook_observation_state"], "observed")
        self.assertEqual(payload["last_observed_at"], "2026-08-21T11:09:00Z")
        self.assertEqual(payload["checks"][-1], {"name": "hook_observation", "state": "pass", "code": "observed"})

    def test_doctor_reports_a_deterministic_bundle_failure_for_a_missing_wheel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            setup = self._setup(data_dir)
            copied_plugin = Path(temp_dir) / "skilltree"
            shutil.copytree(PLUGIN_ROOT, copied_plugin)
            next((copied_plugin / "runtime" / "wheels").glob("*.whl")).unlink()
            state_path = data_dir / "runtime-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["plugin_root"] = str(copied_plugin)
            state_path.write_text(json.dumps(state), encoding="utf-8")
            doctor = self._doctor(data_dir)

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(doctor.returncode, 2, doctor.stderr)
        payload = json.loads(doctor.stdout)
        self.assertFalse(payload["runtime_ready"])
        self.assertEqual(payload["diagnostic_state"], "failed")
        self.assertEqual(payload["checks"][2]["name"], "bundle_manifest")
        self.assertEqual(payload["checks"][2]["state"], "fail")

    def test_doctor_does_not_create_or_modify_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            setup = self._setup(data_dir)
            before = {path.relative_to(data_dir).as_posix(): path.stat().st_mtime_ns for path in data_dir.rglob("*") if path.is_file()}
            doctor = self._doctor(data_dir)
            after = {path.relative_to(data_dir).as_posix(): path.stat().st_mtime_ns for path in data_dir.rglob("*") if path.is_file()}

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(doctor.returncode, 1, doctor.stderr)
        self.assertEqual(after, before)

    def test_doctor_fails_when_the_runtime_state_hook_hash_is_not_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            setup = self._setup(data_dir)
            state_path = data_dir / "runtime-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["hook_bundle_hash"] = "sha256:" + "0" * 64
            state_path.write_text(json.dumps(state), encoding="utf-8")
            doctor = self._doctor(data_dir)

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(doctor.returncode, 2, doctor.stderr)
        payload = json.loads(doctor.stdout)
        self.assertEqual(payload["checks"][5], {"name": "hook_bundle", "state": "fail", "code": "hook_bundle_mismatch"})

    def test_doctor_fails_when_the_installed_core_version_does_not_match_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            setup = self._setup(data_dir)
            state_path = data_dir / "runtime-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["core_version"] = "0.0.0"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            doctor = self._doctor(data_dir)

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(doctor.returncode, 2, doctor.stderr)
        payload = json.loads(doctor.stdout)
        self.assertEqual(payload["checks"][3], {"name": "versions", "state": "fail", "code": "version_mismatch"})

    def test_doctor_keeps_the_fixed_check_contract_when_runtime_state_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            setup = self._setup(data_dir)
            (data_dir / "runtime-state.json").unlink()
            doctor = self._doctor(data_dir)

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(doctor.returncode, 2, doctor.stderr)
        payload = json.loads(doctor.stdout)
        self.assertFalse(payload["runtime_ready"])
        self.assertEqual(payload["diagnostic_state"], "failed")
        self.assertEqual([check["name"] for check in payload["checks"]], [
            "runtime_state", "venv_python", "bundle_manifest", "versions",
            "schema_migrations", "hook_bundle", "hook_observation",
        ])
        self.assertEqual(payload["checks"][0], {"name": "runtime_state", "state": "fail", "code": "runtime_state_missing"})

    def test_doctor_fails_when_the_recorded_migration_hash_is_not_in_the_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            setup = self._setup(data_dir)
            database_path = data_dir / "skilltree.sqlite3"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    "UPDATE schema_migrations SET content_hash = ? WHERE version = 1",
                    ("sha256:" + "0" * 64,),
                )
                connection.commit()
            doctor = self._doctor(data_dir)

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(doctor.returncode, 2, doctor.stderr)
        payload = json.loads(doctor.stdout)
        self.assertEqual(payload["checks"][4], {"name": "schema_migrations", "state": "fail", "code": "migration_mismatch"})
