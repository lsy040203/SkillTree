from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliTests(unittest.TestCase):
    def test_doctor_reports_not_ready_before_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "not-initialized"
            doctor = subprocess.run(
                [sys.executable, "-m", "skilltree", "doctor", "--data-dir", str(data_dir), "--json"],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(doctor.returncode, 2)
        self.assertEqual(json.loads(doctor.stdout)["runtime_ready"], False)

    def test_setup_persists_confirmed_root_and_status_reports_opt_ins(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            skill_root = Path(temp_dir) / "skills"
            skill_root.mkdir()
            setup = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "skilltree",
                    "setup",
                    "--data-dir",
                    str(data_dir),
                    "--skill-root",
                    str(skill_root),
                    "--yes",
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            status = subprocess.run(
                [sys.executable, "-m", "skilltree", "status", "--data-dir", str(data_dir), "--json"],
                text=True,
                capture_output=True,
                check=True,
            )

        self.assertEqual(json.loads(setup.stdout)["skill_root"], str(skill_root.resolve()))
        self.assertEqual(json.loads(status.stdout)["trace_capture_enabled"], False)
