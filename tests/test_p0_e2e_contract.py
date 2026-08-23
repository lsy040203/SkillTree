from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"
UV_PYTHON = os.environ.get(
    "SKILLTREE_UV_PYTHON",
    r"C:\Users\Lenovo\AppData\Roaming\uv\python\cpython-3.14.6-windows-x86_64-none\python.exe",
)


@unittest.skipUnless(Path(UV_PYTHON).is_file(), "requires the configured bootstrap Python")
class P0EndToEndContractTests(unittest.TestCase):
    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def _setup(self, data_dir: Path, plugin_root: Path = PLUGIN_ROOT) -> subprocess.CompletedProcess[str]:
        return self._run([
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(plugin_root / "scripts" / "setup.ps1"), "-PluginData", str(data_dir),
            "-PythonPath", UV_PYTHON,
        ])

    def test_clean_plugin_data_validates_installs_and_reports_p0_degraded_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "plugin-data"
            validated = self._run([
                UV_PYTHON, "-I", str(PLUGIN_ROOT / "runtime" / "bundle_validate.py"), str(PLUGIN_ROOT),
            ])
            setup = self._setup(data_dir)
            doctor = self._run([
                str(data_dir / "venv" / "Scripts" / "python.exe"), "-B", "-I", "-m", "skilltree",
                "doctor", "--data-dir", str(data_dir), "--json",
            ])
            manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
            hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
            payload = json.loads(doctor.stdout)

            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertEqual(setup.returncode, 0, setup.stderr)
            self.assertEqual(doctor.returncode, 1, doctor.stderr)
            self.assertEqual(manifest["name"], "skilltree")
            self.assertEqual(manifest["skills"], "./skills/")
            self.assertNotIn("hooks", manifest)
            self.assertEqual(set(hooks["hooks"]), {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"})
            self.assertTrue((data_dir / "runtime-state.json").is_file())
            self.assertTrue((data_dir / "skilltree.sqlite3").is_file())
            self.assertEqual(payload["schema_version"], "skilltree-doctor/v1")
            self.assertTrue(payload["runtime_ready"])
            self.assertEqual(payload["diagnostic_state"], "degraded")
            self.assertEqual(
                payload["checks"][-1],
                {"name": "hook_observation", "state": "unknown", "code": "hook_unconfirmed"},
            )

    def test_tampered_bundle_is_rejected_before_any_runtime_state_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            copied_plugin = Path(temp_dir) / "skilltree"
            shutil.copytree(PLUGIN_ROOT, copied_plugin)
            next((copied_plugin / "runtime" / "wheels").glob("*.whl")).unlink()
            data_dir = Path(temp_dir) / "plugin-data"
            validated = self._run([
                UV_PYTHON, "-I", str(copied_plugin / "runtime" / "bundle_validate.py"), str(copied_plugin),
            ])
            setup = self._setup(data_dir, copied_plugin)

            self.assertEqual(validated.returncode, 3)
            self.assertEqual(setup.returncode, 3, setup.stderr)
            self.assertEqual(json.loads(setup.stdout)["error"]["code"], "bundle_validation_failed")
            self.assertFalse((data_dir / "runtime-state.json").exists())
            self.assertFalse((data_dir / "venv").exists())
