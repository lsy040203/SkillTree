from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from skilltree.bundle import build_bundle
from skilltree.core.memory_import import import_markdown_candidates
from skilltree.core.memory_store import approve_memory_candidate, list_memory_candidates
from skilltree.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


def test_matching_scenario_orders_relevant_procedure_without_admitting_zero_relevance(tmp_path: Path) -> None:
    from skilltree.core.recall import recall_procedures

    database = _database(tmp_path)
    source = tmp_path / "procedure.md"
    source.write_text(
        "---\nkind: procedure\nscenario_key: p5\nscenario_label: Memory\n"
        "applies_to: repository_analysis\n---\nRead architecture before coding.\n",
        encoding="utf-8",
    )
    import_markdown_candidates(database, source=source, user_id="user-1", workspace_id="workspace-1")
    candidate_id = list_memory_candidates(database, user_id="user-1", workspace_id="workspace-1")[0]["candidate_id"]
    approve_memory_candidate(database, candidate_id=candidate_id, user_id="user-1", workspace_id="workspace-1")

    result = recall_procedures(
        database, query_summary="read architecture before coding", user_id="user-1",
        workspace_id="workspace-1", applies_to="repository_analysis", scenario_key="p5",
    )

    assert result[0]["scenario_key"] == "p5"
    assert result[0]["relevance_score"] > 0
    assert recall_procedures(
        database, query_summary="unrelated database migration", user_id="user-1",
        workspace_id="workspace-1", applies_to="repository_analysis", scenario_key="p5",
    ) == []


def _database(tmp_path: Path) -> Database:
    build_bundle(ROOT)
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=7)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("UPDATE runtime_config SET memory_write_enabled=1, memory_read_enabled=1")
        connection.commit()
    return database
