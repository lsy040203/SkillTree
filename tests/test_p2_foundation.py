from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from skilltree.bundle import build_bundle, validate_bundle
from skilltree.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


def test_current_bundle_preserves_p2_routing_tables_and_adds_only_p3_1_tables() -> None:
    build_bundle(ROOT)
    manifest = validate_bundle(PLUGIN_ROOT)
    assert manifest["plugin"]["version"].split("+", 1)[0] == "0.4.1"
    assert manifest["schema"] == {"version": "skilltree/v1", "migration_version": 9}
    assert [item["path"] for item in manifest["migrations"]] == [
        "migrations/0001_p0_runtime.sql",
        "migrations/0002_p1_registry.sql",
        "migrations/0003_p2_routing.sql",
        "migrations/0004_p3_turn_binding.sql",
        "migrations/0005_p3_trace.sql",
        "migrations/0006_p4_learning.sql",
        "migrations/0007_p5_memory.sql",
        "migrations/0008_p6_replay.sql",
        "migrations/0009_p66_extensions.sql",
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        database = Database(Path(temp_dir) / "skilltree.sqlite3")
        database.initialize(PLUGIN_ROOT, target_schema_version=7)
        with closing(sqlite3.connect(database.path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
    assert tables == {
        "schema_migrations", "runtime_config", "audit_events", "skills",
        "run_contexts", "route_offers", "route_decisions",
        "turn_traces", "run_turn_bindings", "trace_events", "hook_observations",
        "outcome_assessments", "episodes",
            "skill_weights", "skill_weight_updates",
            "memory_write_breakers", "memory_candidates", "profile_fields", "procedures",
        }
