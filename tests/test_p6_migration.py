from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from skilltree.bundle import build_bundle, validate_bundle
from skilltree.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


def _tables(database: Database) -> set[str]:
    with closing(sqlite3.connect(database.path)) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }


def test_migration_eight_creates_only_p6_tables_after_p5(tmp_path: Path) -> None:
    build_bundle(ROOT)
    manifest = validate_bundle(PLUGIN_ROOT)
    assert manifest["schema"]["migration_version"] == 9
    assert manifest["migrations"][-1]["path"] == "migrations/0009_p66_extensions.sql"

    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=8)

    assert database.applied_migrations() == list(range(1, 9))
    assert {
        "replay_capsules",
        "evolution_candidates",
        "evolution_candidate_episode_refs",
        "replay_reports",
    } <= _tables(database)


def test_replay_capsule_state_check_rejects_incomplete_ready_row(tmp_path: Path) -> None:
    build_bundle(ROOT)
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=8)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute(
                "INSERT INTO replay_capsules "
                "(replay_capsule_id,run_id,workspace_id,mode,status,retention_until,created_at) "
                "VALUES ('c','missing','w','fixture_only','ready','2026-01-01','2025-01-01')"
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("incomplete ready capsule must be rejected")
