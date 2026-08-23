from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from skilltree.bundle import build_bundle, validate_bundle
from skilltree.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"
TRACE_TABLES = {"trace_events", "hook_observations", "outcome_assessments", "episodes"}
LEARNING_TABLES = {"skill_weights", "skill_weight_updates"}
MEMORY_TABLES = {"memory_write_breakers", "memory_candidates", "profile_fields", "procedures"}


def _tables(database: Database) -> set[str]:
    with closing(sqlite3.connect(database.path)) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }


def test_latest_bundle_is_contiguous_and_creates_learning_and_memory_tables() -> None:
    build_bundle(ROOT)
    manifest = validate_bundle(PLUGIN_ROOT)
    assert manifest["schema"]["migration_version"] >= 8
    assert manifest["migrations"][6]["path"] == "migrations/0007_p5_memory.sql"

    with tempfile.TemporaryDirectory() as temp_dir:
        database = Database(Path(temp_dir) / "skilltree.sqlite3")
        database.initialize(PLUGIN_ROOT, target_schema_version=7)

        assert database.applied_migrations() == list(range(1, 8))
        assert TRACE_TABLES <= _tables(database)
        assert LEARNING_TABLES <= _tables(database)
        assert MEMORY_TABLES <= _tables(database)

        with closing(sqlite3.connect(database.path)) as connection:
            columns = {row[1]: row[2] for row in connection.execute("PRAGMA table_info(skill_weights)")}
            assert columns["last_signal_at"] == "TEXT"
            assert columns["last_decay_at"] == "TEXT"
            assert columns["rule_version"] == "TEXT"
            update_columns = {row[1]: row[2] for row in connection.execute("PRAGMA table_info(skill_weight_updates)")}
            assert update_columns["evidence_handle"] == "TEXT"
            assert update_columns["evidence_quality"] == "TEXT"


def test_migration_four_prefix_does_not_create_trace_tables() -> None:
    build_bundle(ROOT)
    with tempfile.TemporaryDirectory() as temp_dir:
        database = Database(Path(temp_dir) / "skilltree.sqlite3")
        database.initialize(PLUGIN_ROOT, target_schema_version=4)
        with closing(sqlite3.connect(database.path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        assert database.applied_migrations() == [1, 2, 3, 4]
        assert "trace_events" not in tables
        assert "replay_capsules" not in tables
