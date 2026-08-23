from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from skilltree.application.cli import default_data_dir


class CliTests(unittest.TestCase):
    def test_default_data_dir_uses_plugin_data_for_plugin_processes(self) -> None:
        with patch.dict(os.environ, {"PLUGIN_DATA": r"C:\plugin-data"}, clear=True):
            self.assertEqual(default_data_dir(), Path(r"C:\plugin-data"))

    def test_storage_initialize_applies_manifest_migration_from_explicit_plugin_root(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            result = subprocess.run(
                [
                    sys.executable, "-m", "skilltree", "storage", "initialize",
                    "--data-dir", str(data_dir), "--plugin-root", str(root / "plugins" / "skilltree"),
                    "--target-schema-version", "7", "--json",
                ],
                text=True, capture_output=True, check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "initialized")

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

    def test_registry_commands_use_f7_envelopes_and_do_not_expose_skill_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            skill_root = Path(temp_dir) / "skills"
            skill_file = skill_root / "sample-skill" / "SKILL.md"
            skill_file.parent.mkdir(parents=True)
            skill_file.write_text("---\nname: sample-skill\ndescription: Example skill\n---\n", encoding="utf-8")
            initialized = subprocess.run(
                [
                    sys.executable, "-m", "skilltree", "storage", "initialize",
                    "--data-dir", str(data_dir), "--plugin-root", str(Path(__file__).resolve().parents[1] / "plugins" / "skilltree"),
                    "--target-schema-version", "7", "--json",
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(json.loads(initialized.stdout)["status"], "initialized")
            setup_request = _write_request(
                Path(temp_dir),
                {
                    "schema_version": "skilltree/v1",
                    "user_id": "local",
                    "provided_root": str(skill_root),
                    "selected_root": str(skill_root),
                    "confirm": "SET_SKILL_ROOT",
                },
                "setup.json",
            )
            basic_request = _write_request(
                Path(temp_dir), {"schema_version": "skilltree/v1", "user_id": "local"}, "basic.json"
            )
            setup = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "skilltree",
                    "registry",
                    "setup",
                    "--data-dir",
                    str(data_dir),
                    "--input",
                    str(setup_request),
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            scan = subprocess.run(
                [
                    sys.executable, "-m", "skilltree", "registry", "scan", "--data-dir", str(data_dir),
                    "--input", str(basic_request),
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            status = subprocess.run(
                [
                    sys.executable, "-m", "skilltree", "registry", "status", "--data-dir", str(data_dir),
                    "--input", str(basic_request),
                ],
                text=True,
                capture_output=True,
                check=True,
            )

        setup_payload = json.loads(setup.stdout)
        scan_payload = json.loads(scan.stdout)
        status_payload = json.loads(status.stdout)
        self.assertEqual(setup_payload["schema_version"], "skilltree/v1")
        self.assertTrue(setup_payload["ok"])
        self.assertEqual(scan_payload["data"]["scanned_count"], 1)
        self.assertEqual(status_payload["data"]["skills"][0]["name"], "sample-skill")
        self.assertNotIn(str(skill_root), json.dumps(status_payload))

    def test_registry_invalid_schema_returns_a_safe_error_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = _write_request(Path(temp_dir), {"schema_version": "skilltree/v2", "user_id": "local"}, "bad.json")
            result = subprocess.run(
                [
                    sys.executable, "-m", "skilltree", "registry", "scan", "--data-dir", str(Path(temp_dir) / "data"),
                    "--input", str(request),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload, {
            "schema_version": "skilltree/v1",
            "ok": False,
            "data": None,
            "error": {"code": "invalid_schema", "message": "invalid_schema", "retryable": False},
        })

    def test_route_prepare_and_commit_use_json_file_envelopes_without_exposing_skill_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workspace = "sha256:" + "a" * 64
        session = "sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            data_dir = directory / "data"
            initialized = subprocess.run(
                [
                    sys.executable, "-m", "skilltree", "storage", "initialize",
                    "--data-dir", str(data_dir), "--plugin-root", str(root / "plugins" / "skilltree"),
                    "--target-schema-version", "7", "--json",
                ], text=True, capture_output=True, check=False,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            with closing(sqlite3.connect(data_dir / "skilltree.sqlite3")) as connection:
                connection.execute(
                    "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) VALUES (?, ?, ?, ?, 'trusted', NULL, ?)",
                    ("analyze", "Analyze repositories", "C:/private/analyze/SKILL.md", _hash("analyze"), "2026-08-14T00:00:00Z"),
                )
                connection.commit()
            prepare_request = _write_request(directory, {
                "schema_version": "skilltree/v1", "workspace_id": workspace, "session_id_hash": session,
                "prompt": "analyze this repository",
            }, "route-prepare.json")
            prepared = subprocess.run(
                [sys.executable, "-m", "skilltree", "route", "prepare", "--data-dir", str(data_dir), "--input", str(prepare_request)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            prepare_payload = json.loads(prepared.stdout)
            envelope = prepare_payload["data"]
            commit_request = _write_request(directory, {
                "schema_version": "skilltree-route-commit/v1",
                "route_token": envelope["route_token"],
                "workspace_id": workspace,
                "session_id_hash": session,
                "decision": {
                    "schema_version": "skilltree/v1",
                    "intent": {"name": "repository_analysis", "confidence": 0.9},
                    "constraints": ["read_only"],
                    "ranked_candidates": [{"name": "analyze", "rank": 1, "reason": "best match"}],
                    "selected_skill_name": "analyze",
                    "ordered_skill_names": ["analyze"],
                    "degraded": False,
                },
            }, "route-commit.json")
            committed = subprocess.run(
                [sys.executable, "-m", "skilltree", "route", "commit", "--data-dir", str(data_dir), "--input", str(commit_request)],
                text=True, capture_output=True, check=False,
            )

        self.assertEqual(committed.returncode, 0, committed.stderr)
        self.assertTrue(prepare_payload["ok"])
        self.assertEqual(json.loads(committed.stdout)["data"]["selected_skill_name"], "analyze")
        self.assertNotIn("C:/private", prepared.stdout)
        self.assertNotIn("analyze this repository", prepared.stdout)

    def test_maintenance_sweep_is_an_explicit_user_command(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            initialized = subprocess.run(
                [
                    sys.executable, "-m", "skilltree", "storage", "initialize",
                    "--data-dir", str(data_dir), "--plugin-root", str(root / "plugins" / "skilltree"),
                    "--target-schema-version", "7", "--json",
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            result = subprocess.run(
                [sys.executable, "-m", "skilltree", "maintenance", "sweep", "--data-dir", str(data_dir)],
                text=True, capture_output=True, check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["data"], {
            "audits_purged": 0,
            "candidates_expired": 0,
            "procedures_recomputed": 0,
            "expired_offers_deleted": 0,
            "procedures_hidden": 0,
            "procedures_purged": 0,
            "unrouted_runs_deleted": 0,
            "completed_at": json.loads(result.stdout)["data"]["completed_at"],
        })

    def test_trace_outcome_persists_an_explicit_assessment(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            data_dir = directory / "data"
            initialized = subprocess.run(
                [
                    sys.executable, "-m", "skilltree", "storage", "initialize",
                    "--data-dir", str(data_dir), "--plugin-root", str(root / "plugins" / "skilltree"),
                    "--target-schema-version", "7", "--json",
                ], text=True, capture_output=True, check=False,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            with closing(sqlite3.connect(data_dir / "skilltree.sqlite3")) as connection:
                connection.execute(
                    "INSERT INTO run_contexts VALUES (?, ?, ?, ?, 1, 0, 0, 0, ?, ?)",
                    ("run-1", "sha256:" + "a" * 64, "local", "[]", "2026-08-17T00:00:00Z", "2026-11-15T00:00:00Z"),
                )
                connection.execute(
                    "INSERT INTO turn_traces VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'observed', ?, ?)",
                    ("turn-1", "session-1", "turn-1", "sha256:" + "b" * 64, "sha256:" + "a" * 64,
                     "sha256:" + "c" * 64, "2026-08-17T00:00:00Z", "2026-08-17T00:05:00Z", "sha256:" + "d" * 64,
                     "2026-08-17T00:00:01Z", "2026-11-15T00:00:00Z"),
                )
                connection.execute(
                    "INSERT INTO run_turn_bindings VALUES (?, ?, ?, 'normal')",
                    ("run-1", "turn-1", "2026-08-17T00:00:00Z"),
                )
                connection.commit()
            outcome_request = _write_request(directory, {
                "schema_version": "skilltree-trace-outcome/v1",
                "run_id": "run-1",
                "turn_trace_id": "turn-1",
                "event_id": "outcome-1",
                "source": "user",
                "verdict": "success",
                "outcome_summary": "user confirmed",
                "evidence_ref": "local:user-confirmation",
            }, "outcome.json")
            result = subprocess.run(
                [sys.executable, "-m", "skilltree", "trace", "outcome", "--data-dir", str(data_dir), "--input", str(outcome_request)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            with closing(sqlite3.connect(data_dir / "skilltree.sqlite3")) as connection:
                self.assertEqual(connection.execute("SELECT verdict FROM outcome_assessments").fetchone()[0], "success")


def _write_request(directory: Path, payload: dict[str, object], name: str) -> Path:
    request = directory / name
    request.write_text(json.dumps(payload), encoding="utf-8")
    return request


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
