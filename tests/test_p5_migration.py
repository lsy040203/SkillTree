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


def test_migration_seven_creates_memory_tables(tmp_path: Path) -> None:
    build_bundle(ROOT)
    manifest = validate_bundle(PLUGIN_ROOT)
    assert manifest["schema"]["migration_version"] == 7
    assert manifest["migrations"][-1]["path"] == "migrations/0007_p5_memory.sql"

    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=7)

    assert database.applied_migrations() == [1, 2, 3, 4, 5, 6, 7]
    assert {"memory_write_breakers", "memory_candidates", "profile_fields", "procedures"} <= _tables(database)

