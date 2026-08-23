from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from skilltree.bundle import build_bundle
from skilltree.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


def test_sweep_expires_pending_candidate_and_retains_content_free_audit(tmp_path: Path) -> None:
    from skilltree.core.memory_lifecycle import sweep_memory_lifecycle

    database = _database(tmp_path)
    now = datetime(2026, 8, 22, tzinfo=UTC)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "INSERT INTO memory_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "candidate-expired", None, "workspace-1", "user-1", "profile", "preference",
                "user_global", '{"key":"language","value":"Chinese"}', "sha256:payload",
                "pending", "2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z", "2026-09-01T00:00:00Z",
            ),
        )
        connection.commit()

    result = sweep_memory_lifecycle(database, now=now)

    assert result["candidates_expired"] == 1
    with closing(sqlite3.connect(database.path)) as connection:
        assert connection.execute(
            "SELECT 1 FROM memory_candidates WHERE candidate_id='candidate-expired'"
        ).fetchone() is None
        audit = connection.execute(
            "SELECT event_type,reason_code,retention_until FROM audit_events WHERE event_type='candidate_expired'"
        ).fetchone()
    assert audit[0:2] == ("candidate_expired", "ttl_expired")
    assert audit[2] == "2026-09-21T00:00:00.000Z"


def test_sweep_hides_expired_procedure_then_purges_after_retention(tmp_path: Path) -> None:
    from skilltree.core.memory_lifecycle import sweep_memory_lifecycle

    database = _database(tmp_path)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "INSERT INTO procedures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _procedure_row(
                procedure_id="procedure-expired",
                status="active",
                expires_at="2026-08-01T00:00:00Z",
                hidden_at=None,
                retention_until="2026-09-01T00:00:00Z",
            ),
        )
        connection.commit()

    first = sweep_memory_lifecycle(database, now=datetime(2026, 8, 22, tzinfo=UTC))
    assert first["procedures_hidden"] == 1
    with closing(sqlite3.connect(database.path)) as connection:
        row = connection.execute(
            "SELECT status,hidden_at,retention_until FROM procedures WHERE procedure_id='procedure-expired'"
        ).fetchone()
    assert row == ("hidden", "2026-08-22T00:00:00.000Z", "2026-09-21T00:00:00.000Z")

    second = sweep_memory_lifecycle(database, now=datetime(2026, 9, 22, tzinfo=UTC))
    assert second["procedures_purged"] == 1
    with closing(sqlite3.connect(database.path)) as connection:
        assert connection.execute(
            "SELECT 1 FROM procedures WHERE procedure_id='procedure-expired'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT count(*) FROM audit_events WHERE event_type IN ('procedure_hidden','procedure_purged')"
        ).fetchone()[0] == 2


def test_sweep_is_idempotent_for_already_hidden_and_expired_rows(tmp_path: Path) -> None:
    from skilltree.core.memory_lifecycle import sweep_memory_lifecycle

    database = _database(tmp_path)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "INSERT INTO procedures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _procedure_row(
                procedure_id="procedure-hidden",
                status="hidden",
                expires_at="2026-08-01T00:00:00Z",
                hidden_at="2026-08-01T00:00:00Z",
                retention_until="2026-09-30T00:00:00Z",
            ),
        )
        connection.commit()

    first = sweep_memory_lifecycle(database, now=datetime(2026, 8, 22, tzinfo=UTC))
    second = sweep_memory_lifecycle(database, now=datetime(2026, 8, 22, tzinfo=UTC))

    assert first["procedures_hidden"] == 0
    assert second["procedures_hidden"] == 0
    assert first["procedures_purged"] == second["procedures_purged"] == 0


def test_sweep_upgrades_weak_procedure_using_spec_score(tmp_path: Path) -> None:
    from skilltree.core.memory_lifecycle import sweep_memory_lifecycle

    database = _database(tmp_path)
    with closing(sqlite3.connect(database.path)) as connection:
        row = list(_procedure_row(
            procedure_id="procedure-upgrade", status="active",
            expires_at="2026-12-01T00:00:00Z", hidden_at=None,
            retention_until="2027-01-01T00:00:00Z",
        ))
        row[13] = "weak"
        row[14] = 1.0
        row[15] = 2
        row[18] = 1.0
        row[20] = 0.0
        row[21] = 0
        row[21] = "2026-08-22T00:00:00Z"
        connection.execute("INSERT INTO procedures VALUES (" + ",".join("?" for _ in row) + ")", row)
        connection.commit()

    result = sweep_memory_lifecycle(database, now=datetime(2026, 8, 22, tzinfo=UTC))

    assert result["procedures_recomputed"] == 1
    with closing(sqlite3.connect(database.path)) as connection:
        strength, score, low_sweeps = connection.execute(
            "SELECT strength,score,low_score_sweeps FROM procedures WHERE procedure_id='procedure-upgrade'"
        ).fetchone()
    assert strength == "strong"
    assert score >= 70.0
    assert low_sweeps == 0


def test_sweep_downgrades_strong_only_after_two_low_score_sweeps(tmp_path: Path) -> None:
    from skilltree.core.memory_lifecycle import sweep_memory_lifecycle

    database = _database(tmp_path)
    with closing(sqlite3.connect(database.path)) as connection:
        row = list(_procedure_row(
            procedure_id="procedure-downgrade", status="active",
            expires_at="2027-01-01T00:00:00Z", hidden_at=None,
            retention_until="2027-02-01T00:00:00Z",
        ))
        row[13] = "strong"
        row[15] = 0
        row[18] = 0.0
        row[20] = 0.0
        row[21] = 0
        row[21] = "2026-01-01T00:00:00Z"
        connection.execute("INSERT INTO procedures VALUES (" + ",".join("?" for _ in row) + ")", row)
        connection.commit()

    first = sweep_memory_lifecycle(database, now=datetime(2026, 8, 22, tzinfo=UTC))
    with closing(sqlite3.connect(database.path)) as connection:
        strength, low_sweeps = connection.execute(
            "SELECT strength,low_score_sweeps FROM procedures WHERE procedure_id='procedure-downgrade'"
        ).fetchone()
    assert first["procedures_recomputed"] == 1
    assert (strength, low_sweeps) == ("strong", 1)

    sweep_memory_lifecycle(database, now=datetime(2026, 8, 23, tzinfo=UTC))
    with closing(sqlite3.connect(database.path)) as connection:
        strength, low_sweeps = connection.execute(
            "SELECT strength,low_score_sweeps FROM procedures WHERE procedure_id='procedure-downgrade'"
        ).fetchone()
    assert (strength, low_sweeps) == ("weak", 0)


def test_sweep_does_not_refresh_procedure_expiry(tmp_path: Path) -> None:
    from skilltree.core.memory_lifecycle import sweep_memory_lifecycle

    database = _database(tmp_path)
    original = "2026-12-01T00:00:00Z"
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "INSERT INTO procedures VALUES (" + ",".join("?" for _ in _procedure_row(
                procedure_id="procedure-expiry-stable", status="active", expires_at=original,
                hidden_at=None, retention_until="2027-01-01T00:00:00Z",
            )) + ")",
            _procedure_row(
                procedure_id="procedure-expiry-stable", status="active", expires_at=original,
                hidden_at=None, retention_until="2027-01-01T00:00:00Z",
            ),
        )
        connection.commit()
    sweep_memory_lifecycle(database, now=datetime(2026, 8, 22, tzinfo=UTC))
    with closing(sqlite3.connect(database.path)) as connection:
        assert connection.execute(
            "SELECT expires_at FROM procedures WHERE procedure_id='procedure-expiry-stable'"
        ).fetchone()[0] == original


def test_clear_workspace_data_removes_workspace_graph_and_learning_state(tmp_path: Path) -> None:
    from skilltree.core.memory_lifecycle import clear_workspace_data

    database = _database(tmp_path)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "INSERT INTO run_contexts VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("run-clear", "workspace-1", "user-1", "{}", 1, 1, 1, 0,
             "2026-08-01T00:00:00Z", "2026-11-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO skill_weights VALUES (?,?,?,?,?,?,?,?)",
            ("workspace-1", "analyze", 2, None, None, "2026-08-01T00:00:00Z",
             "2026-08-01T00:00:00Z", "p4/v1"),
        )
        connection.execute(
            "INSERT INTO skill_weight_updates VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("update-clear", "workspace-1", "analyze", "outcome", 1, 0, 1,
             "run:run-clear", "strict", "p4/v1", "2026-08-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO memory_write_breakers VALUES (?,?,?,?,?,?)",
            ("workspace-1", "closed", 0, None, "2026-08-01T00:00:00Z", "2026-11-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO memory_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("candidate-clear", "run-clear", "workspace-1", "user-1", "procedure", "procedure",
             "workspace", '{"rule":"keep"}', "sha256:" + "c" * 64, "pending",
             "2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z", "2026-11-01T00:00:00Z"),
        )
        connection.commit()

    result = clear_workspace_data(database, user_id="user-1", workspace_id="workspace-1")

    assert result["audit_retained_count"] == 3
    with closing(sqlite3.connect(database.path)) as connection:
        for table in ("run_contexts", "skill_weights", "skill_weight_updates",
                      "memory_write_breakers", "memory_candidates", "procedures"):
            assert connection.execute(f"SELECT count(*) FROM {table} WHERE workspace_id=?", ("workspace-1",)).fetchone()[0] == 0


def _database(tmp_path: Path) -> Database:
    build_bundle(ROOT)
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=7)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("UPDATE runtime_config SET memory_write_enabled=1")
        connection.commit()
    return database


def _procedure_row(*, procedure_id: str, status: str, expires_at: str, hidden_at: str | None, retention_until: str) -> tuple[object, ...]:
    return (
        procedure_id, "workspace-1", "user-1", "workspace", "Read source before Python verification.",
        "sha256:" + "a" * 64, "sha256:" + "b" * 64, "repository_verification", "", "",
        "[\"analyze\"]", "[]", "", "weak", 0.5, 0, 1, 0.0, 1.0, 50.0, 0,
        "2026-07-01T00:00:00Z", status, None, None, "2026-07-01T00:00:00Z",
        "2026-07-01T00:00:00Z", expires_at, hidden_at, retention_until,
    )
