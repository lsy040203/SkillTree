from __future__ import annotations

import sqlite3
import hashlib
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from skilltree.bundle import build_bundle
from skilltree.storage import Database, StorageInitializationError


class StorageInitializeTests(unittest.TestCase):
    def setUp(self) -> None:
        build_bundle(ROOT)
        self.plugin_root = ROOT / "plugins" / "skilltree"

    def test_initialize_applies_manifest_migrations_through_p5(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "skilltree.sqlite3")

            result = database.initialize(self.plugin_root, target_schema_version=7)
            with closing(sqlite3.connect(database.path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
            migrations = database.applied_migrations()

        self.assertEqual(result, "initialized")
        self.assertEqual(migrations, list(range(1, 8)))
        self.assertEqual(tables, {"schema_migrations", "runtime_config", "audit_events", "skills", "run_contexts", "route_offers", "route_decisions", "turn_traces", "run_turn_bindings", "trace_events", "hook_observations", "outcome_assessments", "episodes", "skill_weights", "skill_weight_updates", "memory_write_breakers", "memory_candidates", "profile_fields", "procedures"})

    def test_initialize_upgrades_a_p0_database_to_schema_version_two(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "skilltree.sqlite3")
            p0_sql = (self.plugin_root / "migrations" / "0001_p0_runtime.sql").read_text(encoding="utf-8")
            p0_hash = "sha256:" + hashlib.sha256(p0_sql.encode("utf-8")).hexdigest()
            with closing(sqlite3.connect(database.path)) as connection:
                connection.executescript(p0_sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at, content_hash) VALUES (?, ?, ?)",
                    (1, "2026-08-14T00:00:00Z", p0_hash),
                )
                connection.commit()

            result = database.initialize(self.plugin_root, target_schema_version=7)
            with closing(sqlite3.connect(database.path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
            migrations = database.applied_migrations()

        self.assertEqual(result, "initialized")
        self.assertEqual(migrations, [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(tables, {"schema_migrations", "runtime_config", "audit_events", "skills", "run_contexts", "route_offers", "route_decisions", "turn_traces", "run_turn_bindings", "trace_events", "hook_observations", "outcome_assessments", "episodes", "skill_weights", "skill_weight_updates", "memory_write_breakers", "memory_candidates", "profile_fields", "procedures"})

    def test_initialize_rejects_a_recorded_migration_hash_mismatch_without_rewriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "skilltree.sqlite3")
            database.initialize(self.plugin_root, target_schema_version=7)
            with closing(sqlite3.connect(database.path)) as connection:
                connection.execute("UPDATE schema_migrations SET content_hash = 'sha256:' || printf('%064d', 0)")
                connection.commit()

            with self.assertRaisesRegex(StorageInitializationError, "migration hash mismatch"):
                database.initialize(self.plugin_root, target_schema_version=7)

            with closing(sqlite3.connect(database.path)) as connection:
                recorded_hash = connection.execute("SELECT content_hash FROM schema_migrations WHERE version = 1").fetchone()[0]

        self.assertEqual(recorded_hash, "sha256:" + "0" * 64)

    def test_initialize_rejects_a_non_prefix_migration_record_without_rewriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "skilltree.sqlite3")
            database.initialize(self.plugin_root, target_schema_version=7)
            with closing(sqlite3.connect(database.path)) as connection:
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at, content_hash) VALUES (0, '2026-01-01T00:00:00Z', 'sha256:test')"
                )
                connection.commit()

            with self.assertRaisesRegex(StorageInitializationError, "migration history is not a bundle prefix"):
                database.initialize(self.plugin_root, target_schema_version=7)

            with closing(sqlite3.connect(database.path)) as connection:
                recorded_versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]

        self.assertEqual(recorded_versions, [0, 1, 2, 3, 4, 5, 6, 7])
