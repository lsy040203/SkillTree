from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from skilltree.bundle import build_bundle
from skilltree.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"
NOW = "2026-08-19T00:00:00Z"
EVENT_ID = "9bb84d94-1cf2-4bea-865c-5b4073b4c524"
HASH = "sha256:" + "a" * 64


def test_create_candidate_remains_pending_until_approval(tmp_path: Path) -> None:
    from skilltree.core.memory_store import create_memory_candidate

    database = _database(tmp_path)
    result = create_memory_candidate(database, run_id="run-1", candidate=_profile_candidate())

    assert result["status"] == "pending"
    assert _count(database, "memory_candidates") == 1
    assert _count(database, "profile_fields") == 0


def test_approve_candidate_writes_profile_and_removes_pending(tmp_path: Path) -> None:
    from skilltree.core.memory_store import approve_memory_candidate, create_memory_candidate

    database = _database(tmp_path)
    candidate_id = create_memory_candidate(database, run_id="run-1", candidate=_profile_candidate())["candidate_id"]

    result = approve_memory_candidate(
        database, candidate_id=candidate_id, user_id="user-1", workspace_id="workspace-1"
    )

    assert result["layer"] == "profile"
    assert result["action"] == "created"
    assert _count(database, "memory_candidates") == 0
    assert _count(database, "profile_fields") == 1


def test_approval_requires_the_candidate_owner_scope(tmp_path: Path) -> None:
    from skilltree.core.memory_store import MemoryStoreError, approve_memory_candidate, create_memory_candidate

    database = _database(tmp_path)
    candidate_id = create_memory_candidate(database, run_id="run-1", candidate=_profile_candidate())["candidate_id"]

    with pytest.raises(MemoryStoreError, match="out_of_scope"):
        approve_memory_candidate(
            database, candidate_id=candidate_id, user_id="another-user", workspace_id="workspace-1"
        )


def test_exact_profile_value_is_approved_as_a_no_op(tmp_path: Path) -> None:
    from skilltree.core.memory_store import approve_memory_candidate, create_memory_candidate

    database = _database(tmp_path)
    first = create_memory_candidate(database, run_id="run-1", candidate=_profile_candidate())["candidate_id"]
    approve_memory_candidate(database, candidate_id=first, user_id="user-1", workspace_id="workspace-1")
    second = create_memory_candidate(database, run_id="run-1", candidate=_profile_candidate())["candidate_id"]

    result = approve_memory_candidate(
        database, candidate_id=second, user_id="user-1", workspace_id="workspace-1"
    )

    assert result["action"] == "no_op"
    assert _count(database, "profile_fields") == 1


def test_creation_rechecks_runtime_memory_write_consent(tmp_path: Path) -> None:
    from skilltree.core.memory_store import MemoryStoreError, create_memory_candidate

    database = _database(tmp_path)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("UPDATE runtime_config SET memory_write_enabled=0")
        connection.commit()

    with pytest.raises(MemoryStoreError, match="disabled"):
        create_memory_candidate(database, run_id="run-1", candidate=_profile_candidate())


def test_extract_and_store_uses_provider_output_as_pending_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import skilltree.core.memory_store as memory_store

    database = _database(tmp_path)
    monkeypatch.setattr(memory_store, "build_evidence_bundle", lambda _database, run_id: object())
    monkeypatch.setattr(
        memory_store,
        "extract_memory_candidates",
        lambda _bundle, llm: (_profile_candidate(),),
    )

    result = memory_store.extract_and_store_memory_candidates(database, run_id="run-1", llm=object())

    assert result["pending"] == 1
    assert _count(database, "memory_candidates") == 1
    assert _count(database, "profile_fields") == 0


def test_profile_evidence_extraction_creates_pending_candidate_without_active_write(tmp_path: Path) -> None:
    from skilltree.core.memory_store import extract_and_store_profile_candidates

    database = _database(tmp_path)

    class FakeLLM:
        def generate_memory_candidate(self, prompt: dict[str, object]) -> object:
            assert prompt["kind"] == "profile"
            assert prompt["durable_preference_statements"] == ["Always explain in Chinese."]
            return {
                "schema_version": "skilltree/v1",
                "profile_fields": [{
                    "namespace": "preference",
                    "key": "explanation_style",
                    "value": "Chinese explanations",
                    "confidence": 0.9,
                    "reason": "explicit durable preference",
                    "source_kind": "durable_preference_statement",
                    "evidence_event_ids": [],
                }],
                "procedural_candidates": [],
            }

    result = extract_and_store_profile_candidates(
        database,
        user_id="user-1",
        workspace_id="workspace-1",
        durable_preference_statements=("Always explain in Chinese.",),
        llm=FakeLLM(),
    )

    assert result["pending"] == 1
    assert _count(database, "memory_candidates") == 1
    assert _count(database, "profile_fields") == 0


def test_memory_breaker_opens_after_three_infrastructure_failures(tmp_path: Path) -> None:
    from skilltree.core.memory_store import (
        record_memory_write_failure,
    )

    database = _database(tmp_path)
    for _ in range(3):
        record_memory_write_failure(database, workspace_id="workspace-1", reason_code="sqlite_busy")

    with closing(sqlite3.connect(database.path)) as connection:
        row = connection.execute(
            "SELECT state, consecutive_failures, open_until FROM memory_write_breakers WHERE workspace_id=?",
            ("workspace-1",),
        ).fetchone()
    assert row[0] == "open"
    assert row[1] == 3
    assert row[2] is not None


def test_open_memory_breaker_blocks_candidate_write(tmp_path: Path) -> None:
    from skilltree.core.memory_store import MemoryStoreError, create_memory_candidate

    database = _database(tmp_path)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "INSERT INTO memory_write_breakers VALUES (?,?,?,?,?,?)",
            ("workspace-1", "open", 3, "2999-01-01T00:00:00Z", NOW, "2999-02-01T00:00:00Z"),
        )
        connection.commit()

    with pytest.raises(MemoryStoreError, match="memory_write_degraded"):
        create_memory_candidate(database, run_id="run-1", candidate=_profile_candidate())
    assert _count(database, "memory_candidates") == 0


def test_half_open_memory_breaker_allows_only_one_probe_and_recovers(tmp_path: Path) -> None:
    from skilltree.core.memory_store import (
        MemoryStoreError,
        acquire_memory_write_slot,
        record_memory_write_success,
    )

    database = _database(tmp_path)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "INSERT INTO memory_write_breakers VALUES (?,?,?,?,?,?)",
            ("workspace-1", "open", 3, "2000-01-01T00:00:00Z", NOW, "2999-02-01T00:00:00Z"),
        )
        connection.commit()

    assert acquire_memory_write_slot(database, workspace_id="workspace-1") is True
    with pytest.raises(MemoryStoreError, match="memory_write_degraded"):
        acquire_memory_write_slot(database, workspace_id="workspace-1")
    record_memory_write_success(database, workspace_id="workspace-1")

    with closing(sqlite3.connect(database.path)) as connection:
        row = connection.execute(
            "SELECT state, consecutive_failures, open_until FROM memory_write_breakers WHERE workspace_id=?",
            ("workspace-1",),
        ).fetchone()
    assert row == ("closed", 0, None)


def test_infrastructure_failures_trip_breaker_without_counting_schema_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import skilltree.core.memory_store as memory_store
    from skilltree.core.memory_store import MemoryStoreError, create_memory_candidate

    database = _database(tmp_path)
    monkeypatch.setattr(
        memory_store,
        "_insert_pending",
        lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
    )
    for _ in range(2):
        with pytest.raises(MemoryStoreError, match="internal_error"):
            create_memory_candidate(database, run_id="run-1", candidate=_profile_candidate())
    with pytest.raises(MemoryStoreError, match="memory_write_degraded"):
        create_memory_candidate(database, run_id="run-1", candidate=_profile_candidate())

    with closing(sqlite3.connect(database.path)) as connection:
        row = connection.execute(
            "SELECT state, consecutive_failures FROM memory_write_breakers WHERE workspace_id=?",
            ("workspace-1",),
        ).fetchone()
    assert row == ("open", 3)


def _database(tmp_path: Path) -> Database:
    build_bundle(ROOT)
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=7)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("UPDATE runtime_config SET memory_write_enabled=1")
        connection.execute(
            "INSERT INTO run_contexts VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("run-1", "workspace-1", "user-1", "[]", 1, 1, 1, 0, NOW, "2026-11-17T00:00:00Z"),
        )
        decision = {
            "schema_version": "skilltree/v1",
            "intent": {"name": "repository_analysis", "confidence": 1.0},
            "constraints": [],
            "ranked_candidates": [{"name": "analyze", "rank": 1, "reason": "match"}],
            "selected_skill_name": "analyze",
            "ordered_skill_names": ["analyze"],
            "degraded": False,
        }
        connection.execute(
            "INSERT INTO route_decisions VALUES (?,?,?,?,?,?)",
            ("run-1", HASH, HASH, json.dumps(decision), NOW, "2026-11-17T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO trace_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (EVENT_ID, "turn-1", "run-1", 1, "tool_finished", "hook", "observed", NOW, HASH, "read docs", "tool-1", "shell", NOW),
        )
        connection.commit()
    return database


def _profile_candidate() -> dict[str, object]:
    return {
        "schema_version": "skilltree/v1",
        "profile_fields": [{
            "namespace": "preference",
            "key": "explanation_language",
            "value": "Chinese",
            "confidence": 0.9,
            "reason": "explicit durable preference",
            "source_kind": "durable_preference_statement",
            "evidence_event_ids": [EVENT_ID],
        }],
        "procedural_candidates": [],
    }


def _count(database: Database, table: str) -> int:
    with closing(sqlite3.connect(database.path)) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
