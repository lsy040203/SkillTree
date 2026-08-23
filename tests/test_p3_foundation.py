from __future__ import annotations

import sqlite3
import tempfile
import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from skilltree.bundle import build_bundle, validate_bundle
from skilltree.storage import Database, RegistryStorageError


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"
USER_PROMPT_FIXTURE = ROOT / "tests" / "fixtures" / "p3" / "user-prompt-submit-core-v1.json"


def test_p3_bundle_declares_turn_binding_migration_and_installs_only_p3_tables() -> None:
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
        "schema_migrations",
        "runtime_config",
        "audit_events",
        "skills",
        "run_contexts",
        "route_offers",
        "route_decisions",
        "turn_traces",
        "run_turn_bindings",
        "trace_events",
        "hook_observations",
        "outcome_assessments",
        "episodes",
            "skill_weights",
            "skill_weight_updates",
            "memory_write_breakers",
            "memory_candidates",
            "profile_fields",
            "procedures",
        }


def test_host_neutral_user_prompt_fixture_drives_the_p3_1_core_boundary() -> None:
    fixture = json.loads(USER_PROMPT_FIXTURE.read_text(encoding="utf-8"))
    database = _database()

    reserved = database.trace_reserve(**{
        key: fixture[key]
        for key in ("workspace_id", "session_id", "session_id_hash", "turn_id", "prompt_hash", "route_token")
    })

    assert fixture["fixture_version"] == "skilltree-user-prompt-core-fixture/v1"
    assert fixture["source"] == "host_neutral"
    assert reserved["run_id"] is None
    assert _turn_binding_counts(database) == (1, 0, 0)


def test_trace_reserve_binds_a_valid_offer_before_any_tool_and_persists_only_token_hash() -> None:
    database, workspace_id, session_id_hash, envelope = _prepared_route()

    reserved = database.trace_reserve(
        workspace_id=workspace_id,
        session_id="session-1",
        session_id_hash=session_id_hash,
        turn_id="turn-1",
        prompt_hash=_hash("prompt-1"),
        route_token=envelope["route_token"],
    )

    assert set(reserved) == {"turn_trace_id", "turn_token", "run_id", "bind_state"}
    assert reserved["run_id"] is not None
    assert reserved["bind_state"] == "normal"
    with closing(sqlite3.connect(database.path)) as connection:
        trace = connection.execute(
            "SELECT turn_token_hash, consumed_at, coverage_state FROM turn_traces WHERE turn_trace_id = ?",
            (reserved["turn_trace_id"],),
        ).fetchone()
        offer = connection.execute("SELECT provisional_run_id FROM route_offers").fetchone()
        binding = connection.execute(
            "SELECT run_id, turn_trace_id, bind_state FROM run_turn_bindings"
        ).fetchone()
        persisted_values = " ".join(str(value) for row in connection.iterdump() for value in row)

    assert trace == (_hash(reserved["turn_token"]), trace[1], "unattributed")
    assert trace[1] is not None
    assert offer == (reserved["run_id"],)
    assert binding == (reserved["run_id"], reserved["turn_trace_id"], "normal")
    assert reserved["turn_token"] not in persisted_values


def test_trace_reserve_without_an_offer_keeps_only_an_unattributed_turn_trace() -> None:
    database = _database()

    reserved = database.trace_reserve(
        workspace_id="sha256:" + "a" * 64,
        session_id="session-1",
        session_id_hash="sha256:" + "b" * 64,
        turn_id="turn-1",
        prompt_hash=_hash("prompt-1"),
        route_token=None,
    )

    assert reserved["run_id"] is None
    assert reserved["bind_state"] is None
    assert _turn_binding_counts(database) == (1, 0, 0)


def test_trace_reserve_requires_current_trace_capture_consent_before_writing() -> None:
    database = _database(trace_capture_enabled=False)

    with _raises_code("authorization_required"):
        database.trace_reserve(
            workspace_id="sha256:" + "a" * 64,
            session_id="session-1",
            session_id_hash="sha256:" + "b" * 64,
            turn_id="turn-disabled",
            prompt_hash=_hash("prompt-disabled"),
            route_token=None,
        )

    assert _turn_binding_counts(database) == (0, 0, 0)


def test_trace_reserve_rejects_expired_or_replayed_route_tokens_without_an_extra_run() -> None:
    database, workspace_id, session_id_hash, envelope = _prepared_route()
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "UPDATE route_offers SET expires_at = ?",
            (_utc(datetime.now(UTC) - timedelta(seconds=1)),),
        )
        connection.commit()

    with _raises_code("correlation_missing"):
        database.trace_reserve(
            workspace_id=workspace_id, session_id="session-1", session_id_hash=session_id_hash,
            turn_id="turn-expired", prompt_hash=_hash("expired"), route_token=envelope["route_token"],
        )
    assert _turn_binding_counts(database) == (1, 0, 0)

    fresh_database, workspace_id, session_id_hash, fresh_envelope = _prepared_route()
    fresh_database.trace_reserve(
        workspace_id=workspace_id, session_id="session-1", session_id_hash=session_id_hash,
        turn_id="turn-first", prompt_hash=_hash("first"), route_token=fresh_envelope["route_token"],
    )
    with _raises_code("conflict"):
        fresh_database.trace_reserve(
            workspace_id=workspace_id, session_id="session-1", session_id_hash=session_id_hash,
            turn_id="turn-replay", prompt_hash=_hash("replay"), route_token=fresh_envelope["route_token"],
        )
    assert _turn_binding_counts(fresh_database) == (2, 1, 1)


def test_trace_reserve_rejects_cross_workspace_or_session_offers_without_binding() -> None:
    database, workspace_id, session_id_hash, envelope = _prepared_route()

    with _raises_code("correlation_missing"):
        database.trace_reserve(
            workspace_id="sha256:" + "c" * 64, session_id="session-1", session_id_hash=session_id_hash,
            turn_id="turn-workspace", prompt_hash=_hash("workspace"), route_token=envelope["route_token"],
        )
    with _raises_code("correlation_missing"):
        database.trace_reserve(
            workspace_id=workspace_id, session_id="session-2", session_id_hash="sha256:" + "d" * 64,
            turn_id="turn-session", prompt_hash=_hash("session"), route_token=envelope["route_token"],
        )

    assert _turn_binding_counts(database) == (2, 0, 0)


def test_internal_turn_bind_allows_late_bind_before_hard_expiry_and_rejects_hard_expiry() -> None:
    database = _database()
    workspace_id = "sha256:" + "a" * 64
    session_id_hash = "sha256:" + "b" * 64
    trace = database.trace_reserve(
        workspace_id=workspace_id, session_id="session-1", session_id_hash=session_id_hash,
        turn_id="turn-late", prompt_hash=_hash("late"), route_token=None,
    )
    envelope = _prepare_route(database, workspace_id, session_id_hash)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "UPDATE turn_traces SET soft_expires_at = ?, hard_expires_at = ? WHERE turn_trace_id = ?",
            (_utc(datetime.now(UTC) - timedelta(seconds=1)), _utc(datetime.now(UTC) + timedelta(minutes=1)), trace["turn_trace_id"]),
        )
        connection.commit()

    bound = database._bind_turn_trace(
        turn_token=trace["turn_token"], workspace_id=workspace_id, session_id_hash=session_id_hash,
        route_token=envelope["route_token"],
    )
    assert bound["bind_state"] == "late"

    second = database.trace_reserve(
        workspace_id=workspace_id, session_id="session-1", session_id_hash=session_id_hash,
        turn_id="turn-hard", prompt_hash=_hash("hard"), route_token=None,
    )
    second_offer = _prepare_route(database, workspace_id, session_id_hash)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "UPDATE turn_traces SET hard_expires_at = ? WHERE turn_trace_id = ?",
            (_utc(datetime.now(UTC) - timedelta(seconds=1)), second["turn_trace_id"]),
        )
        connection.commit()
    with _raises_code("correlation_missing"):
        database._bind_turn_trace(
            turn_token=second["turn_token"], workspace_id=workspace_id, session_id_hash=session_id_hash,
            route_token=second_offer["route_token"],
        )
    assert _turn_binding_counts(database) == (2, 1, 1)


def test_maintenance_sweep_deletes_expired_offer_then_expired_unrouted_provisional_run() -> None:
    database, workspace_id, session_id_hash, envelope = _prepared_route()
    reserved = database.trace_reserve(
        workspace_id=workspace_id, session_id="session-1", session_id_hash=session_id_hash,
        turn_id="turn-sweep", prompt_hash=_hash("sweep"), route_token=envelope["route_token"],
    )
    expired_at = _utc(datetime.now(UTC) - timedelta(seconds=1))
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("UPDATE route_offers SET expires_at = ?", (expired_at,))
        connection.commit()

    first = database.maintenance_sweep()
    assert first["expired_offers_deleted"] == 1
    assert first["unrouted_runs_deleted"] == 0
    assert _turn_binding_counts(database) == (1, 1, 1)

    expired_at = _utc(datetime.now(UTC) - timedelta(seconds=1))
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "UPDATE run_contexts SET retention_until = ? WHERE run_id = ?",
            (expired_at, reserved["run_id"]),
        )
        connection.execute(
            "UPDATE turn_traces SET retention_until = ? WHERE turn_trace_id = ?",
            (expired_at, reserved["turn_trace_id"]),
        )
        connection.commit()

    second = database.maintenance_sweep()
    with closing(sqlite3.connect(database.path)) as connection:
        audit = connection.execute(
            "SELECT scope, workspace_id, event_type, reason_code FROM audit_events WHERE event_type = 'unrouted_trace_purged'"
        ).fetchone()

    assert second["unrouted_runs_deleted"] == 1
    assert _turn_binding_counts(database) == (0, 0, 0)
    assert audit == ("workspace", workspace_id, "unrouted_trace_purged", "retention_expired")


def test_commit_current_route_resolves_exact_turn_for_compact_fallback() -> None:
    database, workspace_id, session_id_hash, envelope = _prepared_route()
    reserved = database.trace_reserve(
        workspace_id=workspace_id,
        session_id="session-current",
        session_id_hash=session_id_hash,
        turn_id="turn-current",
        prompt_hash=_hash("current"),
        route_token=envelope["route_token"],
    )

    committed = database.commit_current_route(
        workspace_id,
        "session-current",
        "turn-current",
        {"selected_skill_name": "analyze"},
    )

    assert committed["run_id"] == reserved["run_id"]
    with closing(sqlite3.connect(database.path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM route_offers").fetchone()[0] == 0
        stored = connection.execute("SELECT decision_json FROM route_decisions").fetchone()[0]
    assert '"degraded":true' in stored


def test_commit_current_route_preserves_non_degraded_visible_summary() -> None:
    database, workspace_id, session_id_hash, envelope = _prepared_route()
    reserved = database.trace_reserve(
        workspace_id=workspace_id,
        session_id="session-visible",
        session_id_hash=session_id_hash,
        turn_id="turn-visible",
        prompt_hash=_hash("visible"),
        route_token=envelope["route_token"],
    )

    committed = database.commit_current_route(
        workspace_id,
        "session-visible",
        "turn-visible",
        {"selected_skill_name": "analyze", "degraded": False},
    )

    assert committed["run_id"] == reserved["run_id"]
    with closing(sqlite3.connect(database.path)) as connection:
        stored = connection.execute("SELECT decision_json FROM route_decisions").fetchone()[0]
    assert '"degraded":false' in stored


def test_commit_current_route_auto_selects_the_only_offered_candidate() -> None:
    database, workspace_id, session_id_hash, envelope = _prepared_route()
    reserved = database.trace_reserve(
        workspace_id=workspace_id,
        session_id="session-auto",
        session_id_hash=session_id_hash,
        turn_id="turn-auto",
        prompt_hash=_hash("auto"),
        route_token=envelope["route_token"],
    )

    committed = database.commit_current_route(
        workspace_id,
        "session-auto",
        "turn-auto",
        None,
    )

    assert committed["run_id"] == reserved["run_id"]
    with closing(sqlite3.connect(database.path)) as connection:
        stored = connection.execute("SELECT decision_json FROM route_decisions").fetchone()[0]
    assert '"selected_skill_name":"analyze"' in stored
    assert '"degraded":true' in stored


def test_commit_current_route_auto_selects_the_first_ranked_candidate_from_multiple_offers() -> None:
    database = _database()
    workspace_id = "sha256:" + "a" * 64
    session_id_hash = "sha256:" + "b" * 64
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) VALUES (?, ?, ?, ?, 'trusted', NULL, ?)",
            ("analyze", "Analyze repositories", "C:/safe/analyze/SKILL.md", _hash("analyze"), "2026-08-15T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) VALUES (?, ?, ?, ?, 'trusted', NULL, ?)",
            ("lsp", "Inspect language server symbols", "C:/safe/lsp/SKILL.md", _hash("lsp"), "2026-08-15T00:00:00Z"),
        )
        connection.commit()
    envelope = database.prepare_route(workspace_id, session_id_hash, "analyze repository")
    database.trace_reserve(
        workspace_id=workspace_id,
        session_id="session-ranked",
        session_id_hash=session_id_hash,
        turn_id="turn-ranked",
        prompt_hash=_hash("ranked"),
        route_token=envelope["route_token"],
    )

    database.commit_current_route(workspace_id, "session-ranked", None, None)

    with closing(sqlite3.connect(database.path)) as connection:
        stored = connection.execute("SELECT decision_json FROM route_decisions").fetchone()[0]
    assert '"selected_skill_name":"analyze"' in stored
    assert '"ordered_skill_names":["analyze"]' in stored


def test_route_commit_accepts_two_ordered_skills_from_one_multi_candidate_offer() -> None:
    database = _database()
    workspace_id = "sha256:" + "c" * 64
    session_id_hash = "sha256:" + "d" * 64
    with closing(sqlite3.connect(database.path)) as connection:
        connection.executemany(
            "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) VALUES (?, ?, ?, ?, 'trusted', NULL, ?)",
            [
                ("analyze", "Analyze repositories", "C:/safe/analyze/SKILL.md", _hash("analyze"), "2026-08-15T00:00:00Z"),
                ("lsp", "Inspect language server symbols", "C:/safe/lsp/SKILL.md", _hash("lsp"), "2026-08-15T00:00:00Z"),
            ],
        )
        connection.commit()

    envelope = database.prepare_route(workspace_id, session_id_hash, "analyze source")
    database.trace_reserve(
        workspace_id=workspace_id,
        session_id="session-two",
        session_id_hash=session_id_hash,
        turn_id="turn-two",
        prompt_hash=_hash("two"),
        route_token=envelope["route_token"],
    )
    decision = _valid_decision()
    decision["ranked_candidates"] = [
        {"name": "analyze", "rank": 1, "reason": "repository intent"},
        {"name": "lsp", "rank": 2, "reason": "source navigation constraint"},
    ]
    decision["ordered_skill_names"] = ["analyze", "lsp"]

    committed = database.commit_route(envelope["route_token"], workspace_id, session_id_hash, decision)

    assert committed["selected_skill_name"] == "analyze"
    with closing(sqlite3.connect(database.path)) as connection:
        stored = connection.execute("SELECT decision_json FROM route_decisions").fetchone()[0]
    assert '"ordered_skill_names":["analyze","lsp"]' in stored

def test_maintenance_sweep_never_deletes_a_run_that_has_a_route_decision() -> None:
    database, workspace_id, session_id_hash, envelope = _prepared_route()
    reserved = database.trace_reserve(
        workspace_id=workspace_id, session_id="session-1", session_id_hash=session_id_hash,
        turn_id="turn-routed", prompt_hash=_hash("routed"), route_token=envelope["route_token"],
    )
    database.commit_route(envelope["route_token"], workspace_id, session_id_hash, _valid_decision())
    expired_at = _utc(datetime.now(UTC) - timedelta(seconds=1))
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("UPDATE run_contexts SET retention_until = ? WHERE run_id = ?", (expired_at, reserved["run_id"]))
        connection.execute("UPDATE turn_traces SET retention_until = ? WHERE turn_trace_id = ?", (expired_at, reserved["turn_trace_id"]))
        connection.commit()

    result = database.maintenance_sweep()

    assert result["unrouted_runs_deleted"] == 0
    assert _turn_binding_counts(database) == (1, 1, 1)


def _database(*, trace_capture_enabled: bool = True) -> Database:
    build_bundle(ROOT)
    temp_dir = tempfile.TemporaryDirectory()
    database = Database(Path(temp_dir.name) / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=7)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "UPDATE runtime_config SET trace_capture_enabled = ? WHERE config_id = 1",
            (int(trace_capture_enabled),),
        )
        connection.commit()
    database._test_temp_dir = temp_dir  # type: ignore[attr-defined]
    return database


def _prepared_route() -> tuple[Database, str, str, dict[str, object]]:
    database = _database()
    workspace_id = "sha256:" + "a" * 64
    session_id_hash = "sha256:" + "b" * 64
    return database, workspace_id, session_id_hash, _prepare_route(database, workspace_id, session_id_hash)


def _prepare_route(database: Database, workspace_id: str, session_id_hash: str) -> dict[str, object]:
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) "
            "VALUES (?, ?, ?, ?, 'trusted', NULL, ?)",
            ("analyze", "Analyze repositories", "C:/safe/analyze/SKILL.md", _hash("analyze"), "2026-08-15T00:00:00Z"),
        )
        connection.commit()
    return database.prepare_route(workspace_id, session_id_hash, "analyze repository")


def _turn_binding_counts(database: Database) -> tuple[int, int, int]:
    with closing(sqlite3.connect(database.path)) as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("turn_traces", "run_contexts", "run_turn_bindings")
        )


def _hash(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _valid_decision() -> dict[str, object]:
    return {
        "schema_version": "skilltree/v1",
        "intent": {"name": "repository_analysis", "confidence": 0.9},
        "constraints": ["read_only"],
        "ranked_candidates": [{"name": "analyze", "rank": 1, "reason": "best match"}],
        "selected_skill_name": "analyze",
        "ordered_skill_names": ["analyze"],
        "degraded": False,
    }


class _raises_code:
    def __init__(self, code: str) -> None:
        self.code = code

    def __enter__(self) -> _raises_code:
        return self

    def __exit__(self, exception_type, exception, traceback) -> bool:
        return isinstance(exception, RegistryStorageError) and exception.code == self.code
