from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from skilltree.bundle import build_bundle
from skilltree.core.replay_capsules import _contains_rejected, create_replay_capsule, delete_replay_capsule, read_replay_capsule, sweep_replay_capsules
from skilltree.core.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


def test_capsule_fixture_rejects_secret_patterns() -> None:
    assert _contains_rejected({"tool": "api_key=secret-value"}) is True
    assert _contains_rejected({"tool": "read source"}) is False


def test_capsule_fixture_allows_multiline_source_code() -> None:
    assert _contains_rejected({"source": "class Solution:\n    pass\n"}) is False


def test_replay_sweep_is_noop_before_p6_schema(tmp_path: Path) -> None:
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=7)
    assert sweep_replay_capsules(database, data_dir=tmp_path) == {}


def test_ready_capsule_round_trip_and_delete_clears_metadata(tmp_path: Path) -> None:
    build_bundle(ROOT)
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=8)
    run_id, trace_id, episode_id = str(uuid4()), str(uuid4()), str(uuid4())
    now = "2026-08-22T00:00:00Z"
    with sqlite3.connect(database.path) as connection:
        connection.execute("UPDATE runtime_config SET replay_capture_enabled=1")
        connection.execute("INSERT INTO run_contexts(run_id,workspace_id,user_id,snapshot_json,trace_capture_enabled,memory_read_enabled,memory_write_enabled,replay_capture_enabled,created_at,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?)", (run_id, "w", "local", "[{\"name\":\"analyze\",\"content_hash\":\"sha256:" + "a" * 64 + "\"}]", 1, 1, 1, 1, now, "2027-01-01T00:00:00Z"))
        connection.execute("INSERT INTO turn_traces(turn_trace_id,session_id,turn_id,session_id_hash,workspace_id,turn_token_hash,soft_expires_at,hard_expires_at,prompt_hash,coverage_state,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (trace_id, "session", "turn", "sha256:" + "a" * 64, "w", "sha256:" + "d" * 64, "2026-08-23T00:00:00Z", "2026-08-24T00:00:00Z", "sha256:" + "b" * 64, "observed", "2027-01-01T00:00:00Z"))
        connection.execute("INSERT INTO episodes(episode_id,run_id,turn_trace_id,objective_hash,objective_preview,trusted_skill_snapshot,snapshot_partial,trace_state,coverage_state,verdict,event_count,outcome_ref,created_at,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (episode_id, run_id, trace_id, "sha256:" + "c" * 64, "[redacted]", "[{\"name\":\"analyze\"}]", 0, "complete", "observed", "success", 1, None, now, "2027-01-01T00:00:00Z"))
    capsule = create_replay_capsule(database, data_dir=tmp_path, run_id=run_id, consent_id=str(uuid4()), fixture={"tool_fixtures": [{"name": "read_source"}]})
    assert capsule["status"] == "ready"
    assert read_replay_capsule(tmp_path, capsule["replay_capsule_id"])["fixture"]
    delete_replay_capsule(database, data_dir=tmp_path, capsule_id=capsule["replay_capsule_id"])
    with sqlite3.connect(database.path) as connection:
        row = connection.execute("SELECT status,consent_id,blob_handle,content_hash,expires_at FROM replay_capsules").fetchone()
    assert row == ("deleted", None, None, None, None)
