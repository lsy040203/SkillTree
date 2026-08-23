from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from skilltree.bundle import build_bundle
from skilltree.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


def test_frontmatter_markdown_becomes_pending_procedure(tmp_path: Path) -> None:
    from skilltree.core.memory_import import import_markdown_candidates

    database = _database(tmp_path)
    source = tmp_path / "experience.md"
    source.write_text(
        "---\nkind: procedure\nscenario_key: p5\nscenario_label: Memory design\n"
        "applies_to: memory_design\n---\nRead the project structure before changing code.\n",
        encoding="utf-8",
    )

    result = import_markdown_candidates(
        database, source=source, user_id="user-1", workspace_id="workspace-1"
    )

    assert result["created"] == 1
    assert "path" not in result
    assert _count(database, "memory_candidates") == 1


def test_markdown_import_rejects_oversized_input_without_writing(tmp_path: Path) -> None:
    from skilltree.core.memory_import import MemoryImportError, import_markdown_candidates

    database = _database(tmp_path)
    source = tmp_path / "large.md"
    source.write_text("x" * (64 * 1024 + 1), encoding="utf-8")

    try:
        import_markdown_candidates(database, source=source, user_id="user-1", workspace_id="workspace-1")
    except MemoryImportError as error:
        assert error.code == "invalid_schema"
    else:
        raise AssertionError("oversized Markdown must be rejected")
    assert _count(database, "memory_candidates") == 0


def _database(tmp_path: Path) -> Database:
    build_bundle(ROOT)
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=7)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("UPDATE runtime_config SET memory_write_enabled=1")
        connection.commit()
    return database


def _count(database: Database, table: str) -> int:
    with closing(sqlite3.connect(database.path)) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
