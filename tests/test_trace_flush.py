from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from skilltree.bundle import build_bundle
from skilltree.core.outbox import AtomicOutbox, WriterLease
from skilltree.core.trace_events import build_trace_event
from skilltree.core.trace_flush import flush_trace_events
from skilltree.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


def _event(event_id: str, payload_hash: str = "sha256:" + "a" * 64, source: str = "hook") -> dict[str, object]:
    return build_trace_event(
        event_id=event_id, turn_trace_id="turn-1", run_id="run-1", event_type="tool_started",
        source=source, coverage_state="observed", observed_at="2026-08-17T00:00:00Z",
        payload_summary="started", payload_hash=payload_hash, tool_use_id=event_id, tool_name="bash",
    )


def _database(root: Path) -> Database:
    if not any((PLUGIN_ROOT / "runtime" / "wheels").glob("*.whl")):
        build_bundle(ROOT)
    database = Database(root / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=7)
    with closing(sqlite3.connect(database.path)) as connection:
        workspace = "sha256:" + "a" * 64
        connection.execute("INSERT INTO run_contexts VALUES (?, ?, ?, ?, 1, 0, 0, 0, ?, ?)", ("run-1", workspace, "user", "{}", "2026-08-17T00:00:00Z", "2026-09-17T00:00:00Z"))
        connection.execute("INSERT INTO turn_traces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("turn-1", "session", "turn", "sha256:" + "b" * 64, workspace, "sha256:" + "c" * 64, "2026-08-18T00:00:00Z", "2026-08-19T00:00:00Z", None, "sha256:" + "d" * 64, "observed", None, "2026-09-17T00:00:00Z"))
        connection.execute("INSERT INTO run_turn_bindings VALUES (?, ?, ?, ?)", ("run-1", "turn-1", "2026-08-17T00:00:00Z", "normal"))
        connection.commit()
    return database


def test_find_turn_trace_does_not_cross_session_or_turn_boundaries() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        database = _database(Path(temp_dir))
        workspace = "sha256:" + "a" * 64
        resolved = database.find_turn_trace(workspace, "session", "host-turn-mismatch")
        assert resolved is None
        assert database.find_turn_trace(workspace, "host-session-mismatch", "host-turn-mismatch") is None
        assert database.find_turn_trace("sha256:" + "z" * 64, "session", "host-turn-mismatch") is None


def test_flush_inserts_in_sequence_and_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        database = _database(root)
        outbox = AtomicOutbox(root / "outbox")
        outbox.enqueue_trace_event(_event("event-1"))
        now = datetime.now(UTC)
        report = flush_trace_events(database, outbox, WriterLease(root / "lease", owner_id="a", ttl_seconds=10), now=now)
        again = flush_trace_events(database, outbox, WriterLease(root / "lease", owner_id="a", ttl_seconds=10), now=now)
        assert report.inserted == 1
        assert again.inserted == 0
        with closing(sqlite3.connect(database.path)) as connection:
            assert connection.execute("SELECT COUNT(*), MIN(ingest_sequence) FROM trace_events").fetchone() == (1, 1)


def test_conflicting_event_is_quarantined_and_not_overwritten() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        database = _database(root)
        outbox = AtomicOutbox(root / "outbox")
        outbox.enqueue_trace_event(_event("event-1"))
        flush_trace_events(database, outbox, WriterLease(root / "lease", owner_id="a", ttl_seconds=10), now=datetime.now(UTC))
        outbox.enqueue_trace_event(_event("event-1", "sha256:" + "e" * 64))
        report = flush_trace_events(database, outbox, WriterLease(root / "lease", owner_id="a", ttl_seconds=10), now=datetime.now(UTC))
        assert report.quarantined == 1
        assert list((root / "outbox" / "quarantine").glob("*.json"))


def test_flush_records_hook_observation_without_reading_raw_hook_payload() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        database = _database(root)
        outbox = AtomicOutbox(root / "outbox")
        hook_hash = "sha256:" + "f" * 64
        outbox.enqueue_trace_event(_event("event-1", source=f"hook:{hook_hash}"))
        flush_trace_events(database, outbox, WriterLease(root / "lease", owner_id="a", ttl_seconds=10), now=datetime.now(UTC))
        with closing(sqlite3.connect(database.path)) as connection:
            assert connection.execute("SELECT observed_count, last_event_id FROM hook_observations WHERE hook_bundle_hash=?", (hook_hash,)).fetchone() == (1, "event-1")


def test_flush_run_closed_closes_turn_trace_idempotently() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        database = _database(root)
        outbox = AtomicOutbox(root / "outbox")
        outbox.enqueue_trace_event(build_trace_event(
            event_id="closed", turn_trace_id="turn-1", run_id="run-1", event_type="run_closed",
            source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:00Z",
            payload_summary="stop", payload_hash="sha256:" + "f" * 64,
        ))
        report = flush_trace_events(database, outbox, WriterLease(root / "lease", owner_id="a", ttl_seconds=10), now=datetime.now(UTC))
        assert report.inserted == 1
        with closing(sqlite3.connect(database.path)) as connection:
            closed_at = connection.execute("SELECT closed_at FROM turn_traces WHERE turn_trace_id='turn-1'").fetchone()[0]
        assert closed_at == "2026-08-17T00:00:00Z"


def test_flush_assembles_a_complete_episode_after_close(tmp_path: Path) -> None:
    database = _database(tmp_path)
    outbox = AtomicOutbox(tmp_path / "outbox")
    outbox.enqueue_trace_event(build_trace_event(
        event_id="start", turn_trace_id="turn-1", run_id="run-1", event_type="tool_started",
        source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:00Z",
        payload_summary="start", payload_hash="sha256:" + "a" * 64, tool_use_id="tool-1", tool_name="bash",
    ))
    outbox.enqueue_trace_event(build_trace_event(
        event_id="finish", turn_trace_id="turn-1", run_id="run-1", event_type="tool_finished",
        source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:01Z",
        payload_summary="finish", payload_hash="sha256:" + "b" * 64, tool_use_id="tool-1", tool_name="bash",
    ))
    outbox.enqueue_trace_event(build_trace_event(
        event_id="closed", turn_trace_id="turn-1", run_id="run-1", event_type="run_closed",
        source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:02Z",
        payload_summary="stop", payload_hash="sha256:" + "c" * 64,
    ))
    flush_trace_events(database, outbox, WriterLease(tmp_path / "lease", owner_id="a", ttl_seconds=10), now=datetime.now(UTC))
    with closing(sqlite3.connect(database.path)) as connection:
        assert connection.execute("SELECT count(*) FROM episodes").fetchone()[0] == 1


def test_flush_defers_episode_assembly_during_close_grace(tmp_path: Path) -> None:
    database = _database(tmp_path)
    outbox = AtomicOutbox(tmp_path / "outbox")
    outbox.enqueue_trace_event(build_trace_event(
        event_id="closed", turn_trace_id="turn-1", run_id="run-1", event_type="run_closed",
        source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:02Z",
        payload_summary="stop", payload_hash="sha256:" + "f" * 64,
    ))
    flush_trace_events(
        database, outbox,
        WriterLease(tmp_path / "lease", owner_id="writer", ttl_seconds=10),
        now=datetime.fromisoformat("2026-08-17T00:00:03+00:00"),
    )
    with closing(sqlite3.connect(database.path)) as connection:
        assert connection.execute("SELECT count(*) FROM episodes").fetchone()[0] == 0

    flush_trace_events(
        database, outbox,
        WriterLease(tmp_path / "lease", owner_id="writer", ttl_seconds=10),
        now=datetime.fromisoformat("2026-08-17T00:00:08+00:00"),
    )
    with closing(sqlite3.connect(database.path)) as connection:
        assert connection.execute("SELECT count(*) FROM episodes").fetchone()[0] == 1
