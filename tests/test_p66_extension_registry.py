from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from skilltree.bundle import build_bundle, validate_bundle
from skilltree.core.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


def test_migration_nine_creates_extension_registry(tmp_path: Path) -> None:
    build_bundle(ROOT)
    manifest = validate_bundle(PLUGIN_ROOT)
    assert manifest["schema"]["migration_version"] == 9
    assert manifest["migrations"][-1]["path"] == "migrations/0009_p66_extensions.sql"
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=9)
    with closing(sqlite3.connect(database.path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(replay_extensions)")}
    assert {
        "extension_id", "extension_version", "adapter_name", "task_types_json",
        "manifest_hash", "image_name", "image_digest", "trust_state",
        "install_state", "installed_at", "updated_at",
    } <= columns
