from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from skilltree.consent_io import ConsentInputError, load_consent_request
from skilltree.storage import Database, RegistryStorageError


PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


class ConsentInputContractTests(unittest.TestCase):
    def test_status_accepts_the_exact_local_user_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "status.json"
            request_path.write_text(
                json.dumps({"schema_version": "skilltree/v1", "user_id": "local"}),
                encoding="utf-8",
            )

            request = load_consent_request(request_path, "status")

        self.assertEqual(request, {"schema_version": "skilltree/v1", "user_id": "local"})

    def test_set_consent_accepts_the_complete_expected_state(self) -> None:
        payload = {
            "schema_version": "skilltree/v1",
            "user_id": "local",
            "expected_config_version": 1,
            "consents": {
                "trace_capture_enabled": True,
                "memory_read_enabled": False,
                "memory_write_enabled": False,
                "replay_capture_enabled": False,
            },
            "confirm": "SET_RUNTIME_CONSENT",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "set-consent.json"
            request_path.write_text(json.dumps(payload), encoding="utf-8")

            request = load_consent_request(request_path, "set-consent")

        self.assertEqual(request, payload)

    def test_parser_rejects_duplicate_keys_and_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            duplicate = Path(temp_dir) / "duplicate.json"
            duplicate.write_text(
                '{"schema_version":"skilltree/v1","user_id":"local","user_id":"local"}',
                encoding="utf-8",
            )
            unknown = Path(temp_dir) / "unknown.json"
            unknown.write_text(
                json.dumps({"schema_version": "skilltree/v1", "user_id": "local", "extra": False}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConsentInputError, "invalid_schema"):
                load_consent_request(duplicate, "status")
            with self.assertRaisesRegex(ConsentInputError, "invalid_schema"):
                load_consent_request(unknown, "status")

    def test_parser_rejects_invalid_consent_values(self) -> None:
        payload = {
            "schema_version": "skilltree/v1",
            "user_id": "local",
            "expected_config_version": 1,
            "consents": {
                "trace_capture_enabled": True,
                "memory_read_enabled": False,
                "memory_write_enabled": False,
                "replay_capture_enabled": False,
            },
            "confirm": "WRONG",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "invalid.json"
            request_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ConsentInputError, "invalid_schema"):
                load_consent_request(request_path, "set-consent")


class RuntimeConsentStorageTests(unittest.TestCase):
    def test_set_consent_updates_changed_keys_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "skilltree.sqlite3")
            database.initialize(PLUGIN_ROOT, target_schema_version=7)
            desired = {
                "trace_capture_enabled": True,
                "memory_read_enabled": False,
                "memory_write_enabled": False,
                "replay_capture_enabled": False,
            }

            changed = database.set_runtime_consent(1, desired)
            repeated = database.set_runtime_consent(2, desired)
            with closing(sqlite3.connect(database.path)) as connection:
                audits = list(connection.execute(
                    "SELECT event_type, object_handle_hash, reason_code, policy_version "
                    "FROM audit_events WHERE event_type = 'runtime_consent_changed'"
                ))

        self.assertEqual(changed["config_version"], 2)
        self.assertEqual(changed["changed_keys"], ["trace_capture_enabled"])
        self.assertEqual(repeated["config_version"], 2)
        self.assertEqual(repeated["changed_keys"], [])
        self.assertEqual(changed["consents"], desired)
        self.assertEqual(audits, [
            ("runtime_consent_changed", _hash("runtime_config/trace_capture_enabled"), "enabled", "runtime-consent/v1")
        ])

    def test_set_consent_rejects_a_stale_config_version_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "skilltree.sqlite3")
            database.initialize(PLUGIN_ROOT, target_schema_version=7)
            current = database.runtime_consent_status()

            with self.assertRaisesRegex(RegistryStorageError, "conflict"):
                database.set_runtime_consent(2, current["consents"])

            after = database.runtime_consent_status()

        self.assertEqual(after, current)


class ConsentCliContractTests(unittest.TestCase):
    def test_config_status_returns_the_f7_envelope_without_mutating_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            data_dir = directory / "data"
            database = Database(data_dir / "skilltree.sqlite3")
            database.initialize(PLUGIN_ROOT, target_schema_version=7)
            request_path = directory / "status.json"
            request_path.write_text(
                json.dumps({"schema_version": "skilltree/v1", "user_id": "local"}),
                encoding="utf-8",
            )

            result = _run_cli("config", "status", "--data-dir", str(data_dir), "--input", str(request_path))
            stored = database.runtime_consent_status()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "schema_version": "skilltree/v1",
            "ok": True,
            "data": stored,
            "error": None,
        })

    def test_config_set_consent_returns_changed_keys_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            data_dir = directory / "data"
            database = Database(data_dir / "skilltree.sqlite3")
            database.initialize(PLUGIN_ROOT, target_schema_version=7)
            request_path = directory / "set-consent.json"
            request_path.write_text(json.dumps(_set_consent_request(1)), encoding="utf-8")

            changed = _run_cli("config", "set-consent", "--data-dir", str(data_dir), "--input", str(request_path))
            request_path.write_text(json.dumps(_set_consent_request(2)), encoding="utf-8")
            repeated = _run_cli("config", "set-consent", "--data-dir", str(data_dir), "--input", str(request_path))

        changed_payload = json.loads(changed.stdout)
        repeated_payload = json.loads(repeated.stdout)
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertTrue(changed_payload["ok"])
        self.assertEqual(changed_payload["data"]["config_version"], 2)
        self.assertEqual(changed_payload["data"]["changed_keys"], ["trace_capture_enabled"])
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertTrue(repeated_payload["ok"])
        self.assertEqual(repeated_payload["data"]["config_version"], 2)
        self.assertEqual(repeated_payload["data"]["changed_keys"], [])

    def test_config_set_consent_reports_conflict_without_disclosing_the_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            data_dir = directory / "data"
            database = Database(data_dir / "skilltree.sqlite3")
            database.initialize(PLUGIN_ROOT, target_schema_version=7)
            request_path = directory / "sensitive-request.json"
            request_path.write_text(json.dumps(_set_consent_request(2)), encoding="utf-8")

            result = _run_cli("config", "set-consent", "--data-dir", str(data_dir), "--input", str(request_path))

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(set(payload), {"schema_version", "ok", "data", "error"})
        self.assertEqual(payload["error"]["code"], "conflict")
        self.assertNotIn("sensitive-request.json", result.stdout)
        self.assertNotIn(str(request_path), result.stdout)

    def test_config_set_consent_rejects_invalid_schema_without_disclosing_the_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            data_dir = directory / "data"
            database = Database(data_dir / "skilltree.sqlite3")
            database.initialize(PLUGIN_ROOT, target_schema_version=7)
            request_path = directory / "invalid-request.json"
            request_path.write_text(
                json.dumps({"schema_version": "skilltree/v1", "user_id": "local", "secret": "do-not-echo"}),
                encoding="utf-8",
            )

            result = _run_cli("config", "set-consent", "--data-dir", str(data_dir), "--input", str(request_path))

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(set(payload), {"schema_version", "ok", "data", "error"})
        self.assertEqual(payload["error"]["code"], "invalid_schema")
        self.assertNotIn("invalid-request.json", result.stdout)
        self.assertNotIn("do-not-echo", result.stdout)


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "skilltree", *arguments],
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
    )


def _hash(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _set_consent_request(version: int) -> dict[str, object]:
    return {
        "schema_version": "skilltree/v1",
        "user_id": "local",
        "expected_config_version": version,
        "consents": {
            "trace_capture_enabled": True,
            "memory_read_enabled": False,
            "memory_write_enabled": False,
            "replay_capture_enabled": False,
        },
        "confirm": "SET_RUNTIME_CONSENT",
    }
