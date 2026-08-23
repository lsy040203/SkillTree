from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from skilltree.core.episode import assemble_episode, close_turn_trace, record_outcome_assessment
from skilltree.core.outbox import AtomicOutbox, WriterLease
from skilltree.core.trace_events import build_trace_event
from skilltree.core.trace_flush import flush_trace_events
from skilltree.core.trajectory import read_session_trajectory, read_turn_trajectory
from tests.test_trace_flush import _database


def test_closed_observed_trace_assembles_once_and_projects_read_only(tmp_path: Path) -> None:
    database = _database(tmp_path)
    outbox = AtomicOutbox(tmp_path / "outbox")
    event = build_trace_event(event_id="closed", turn_trace_id="turn-1", run_id="run-1", event_type="run_closed", source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:00Z", payload_summary="stop", payload_hash="sha256:" + "f" * 64)
    outbox.enqueue_trace_event(event)
    flush_trace_events(database, outbox, WriterLease(tmp_path / "lease", owner_id="writer", ttl_seconds=10), now=datetime.now(UTC))
    assert close_turn_trace(database, turn_trace_id="turn-1", observed_at="2026-08-17T00:00:00Z")
    first = assemble_episode(database, turn_trace_id="turn-1")
    second = assemble_episode(database, turn_trace_id="turn-1")
    projection = read_turn_trajectory(database, "turn-1")
    assert first.status == second.status == "assembled"
    assert first.episode_id == second.episode_id
    assert projection is not None and projection.trace_state == "complete" and projection.verdict == "unknown"


def test_explicit_outcome_is_idempotent_and_visible_to_projection(tmp_path: Path) -> None:
    database = _database(tmp_path)
    assessment_id = record_outcome_assessment(
        database, run_id="run-1", turn_trace_id="turn-1", event_id="assessment-event",
        source="user", verdict="success", outcome_summary="user confirmed", observed_at="2026-08-17T00:00:00Z",
    )
    assert record_outcome_assessment(
        database, run_id="run-1", turn_trace_id="turn-1", event_id="assessment-event",
        source="user", verdict="success", outcome_summary="user confirmed", observed_at="2026-08-17T00:00:00Z",
    ) == assessment_id
    assert read_turn_trajectory(database, "turn-1").verdict == "success"


def test_late_outcome_updates_existing_episode_verdict(tmp_path: Path) -> None:
    database = _database(tmp_path)
    outbox = AtomicOutbox(tmp_path / "outbox")
    outbox.enqueue_trace_event(build_trace_event(
        event_id="closed", turn_trace_id="turn-1", run_id="run-1", event_type="run_closed",
        source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:00Z",
        payload_summary="stop", payload_hash="sha256:" + "f" * 64,
    ))
    flush_trace_events(
        database, outbox,
        WriterLease(tmp_path / "lease", owner_id="writer", ttl_seconds=10),
        now=datetime.now(UTC),
    )
    close_turn_trace(database, turn_trace_id="turn-1", observed_at="2026-08-17T00:00:00Z")
    episode = assemble_episode(database, turn_trace_id="turn-1")
    assert episode.episode_id is not None

    assessment_id = record_outcome_assessment(
        database, run_id="run-1", turn_trace_id="turn-1", event_id="late-outcome",
        source="user", verdict="success", outcome_summary="user confirmed",
        observed_at="2026-08-17T00:00:01Z",
    )

    with sqlite3.connect(database.path) as connection:
        assert connection.execute(
            "SELECT verdict, outcome_ref FROM episodes WHERE episode_id=?",
            (episode.episode_id,),
        ).fetchone() == ("success", assessment_id)


def test_unmatched_tool_event_keeps_episode_and_projection_incomplete(tmp_path: Path) -> None:
    database = _database(tmp_path)
    outbox = AtomicOutbox(tmp_path / "outbox")
    for event in (
        build_trace_event(event_id="start", turn_trace_id="turn-1", run_id="run-1", event_type="tool_started", source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:00Z", payload_summary="start", payload_hash="sha256:" + "a" * 64, tool_use_id="tool-1", tool_name="bash"),
        build_trace_event(event_id="stop", turn_trace_id="turn-1", run_id="run-1", event_type="run_closed", source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:01Z", payload_summary="stop", payload_hash="sha256:" + "b" * 64),
    ):
        outbox.enqueue_trace_event(event)
    flush_trace_events(database, outbox, WriterLease(tmp_path / "lease", owner_id="writer", ttl_seconds=10), now=datetime.now(UTC))
    close_turn_trace(database, turn_trace_id="turn-1", observed_at="2026-08-17T00:00:01Z")
    assert assemble_episode(database, turn_trace_id="turn-1").status == "assembled"
    assert read_turn_trajectory(database, "turn-1").trace_state == "incomplete"


def test_late_tool_finish_upgrades_existing_incomplete_episode(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with sqlite3.connect(database.path) as connection:
        connection.execute("UPDATE run_contexts SET snapshot_json='[\"trusted-skill\"]' WHERE run_id='run-1'")
        connection.commit()
    outbox = AtomicOutbox(tmp_path / "outbox")
    outbox.enqueue_trace_event(build_trace_event(
        event_id="start", turn_trace_id="turn-1", run_id="run-1", event_type="tool_started",
        source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:00Z",
        payload_summary="start", payload_hash="sha256:" + "a" * 64, tool_use_id="tool-1", tool_name="bash",
    ))
    outbox.enqueue_trace_event(build_trace_event(
        event_id="stop", turn_trace_id="turn-1", run_id="run-1", event_type="run_closed",
        source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:01Z",
        payload_summary="stop", payload_hash="sha256:" + "b" * 64,
    ))
    flush_trace_events(database, outbox, WriterLease(tmp_path / "lease", owner_id="writer", ttl_seconds=10), now=datetime.now(UTC))
    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT trace_state FROM episodes").fetchone() == ("incomplete",)

    outbox.enqueue_trace_event(build_trace_event(
        event_id="finish", turn_trace_id="turn-1", run_id="run-1", event_type="tool_finished",
        source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:02Z",
        payload_summary="finish", payload_hash="sha256:" + "c" * 64, tool_use_id="tool-1", tool_name="bash",
    ))
    flush_trace_events(database, outbox, WriterLease(tmp_path / "lease", owner_id="writer", ttl_seconds=10), now=datetime.now(UTC))

    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT trace_state,event_count FROM episodes").fetchone() == ("complete", 3)
    assert read_turn_trajectory(database, "turn-1").trace_state == "complete"


def test_tool_failed_is_a_terminal_observed_phase(tmp_path: Path) -> None:
    database = _database(tmp_path)
    outbox = AtomicOutbox(tmp_path / "outbox")
    for event in (
        build_trace_event(
            event_id="start", turn_trace_id="turn-1", run_id="run-1", event_type="tool_started",
            source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:00Z",
            payload_summary="start", payload_hash="sha256:" + "a" * 64, tool_use_id="tool-1", tool_name="bash",
        ),
        build_trace_event(
            event_id="failed", turn_trace_id="turn-1", run_id="run-1", event_type="tool_failed",
            source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:01Z",
            payload_summary="failed", payload_hash="sha256:" + "b" * 64, tool_use_id="tool-1", tool_name="bash",
        ),
        build_trace_event(
            event_id="stop", turn_trace_id="turn-1", run_id="run-1", event_type="run_closed",
            source="hook", coverage_state="observed", observed_at="2026-08-17T00:00:02Z",
            payload_summary="stop", payload_hash="sha256:" + "c" * 64,
        ),
    ):
        outbox.enqueue_trace_event(event)

    flush_trace_events(
        database, outbox,
        WriterLease(tmp_path / "lease", owner_id="writer", ttl_seconds=10),
        now=datetime.now(UTC),
    )

    assert read_turn_trajectory(database, "turn-1").trace_state == "complete"


def test_out_of_order_tool_delivery_still_assembles_by_tool_use_id(tmp_path: Path) -> None:
    database = _database(tmp_path)
    outbox = AtomicOutbox(tmp_path / "outbox")
    # Simulate concurrent Hook processes: PostToolUse reaches the outbox
    # before PreToolUse, while the host-provided ID remains stable.
    for event in (
        build_trace_event(
            event_id="finish-first", turn_trace_id="turn-1", run_id="run-1",
            event_type="tool_finished", source="hook", coverage_state="observed",
            observed_at="2026-08-17T00:00:01Z", payload_summary="finish",
            payload_hash="sha256:" + "b" * 64, tool_use_id="tool-1", tool_name="bash",
        ),
        build_trace_event(
            event_id="start-late", turn_trace_id="turn-1", run_id="run-1",
            event_type="tool_started", source="hook", coverage_state="observed",
            observed_at="2026-08-17T00:00:00Z", payload_summary="start",
            payload_hash="sha256:" + "a" * 64, tool_use_id="tool-1", tool_name="bash",
        ),
        build_trace_event(
            event_id="closed", turn_trace_id="turn-1", run_id="run-1",
            event_type="run_closed", source="hook", coverage_state="observed",
            observed_at="2026-08-17T00:00:02Z", payload_summary="stop",
            payload_hash="sha256:" + "c" * 64,
        ),
    ):
        outbox.enqueue_trace_event(event)

    flush_trace_events(
        database, outbox,
        WriterLease(tmp_path / "lease", owner_id="writer", ttl_seconds=10),
        now=datetime.now(UTC),
    )

    trajectory = read_turn_trajectory(database, "turn-1")
    assert trajectory is not None and trajectory.trace_state == "complete"


def test_session_projection_is_read_only_and_groups_persisted_turns(tmp_path: Path) -> None:
    database = _database(tmp_path)
    before = database.path.read_bytes()
    session = read_session_trajectory(database, "session")
    after = database.path.read_bytes()
    assert session is not None
    assert session.session_id == "session"
    assert [turn.turn_trace_id for turn in session.turns] == ["turn-1"]
    assert before == after
    assert read_session_trajectory(database, "missing") is None


def test_closed_partial_turn_creates_diagnostic_incomplete_episode(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with sqlite3.connect(database.path) as connection:
        connection.execute("UPDATE turn_traces SET coverage_state='partial' WHERE turn_trace_id='turn-1'")
        connection.execute("UPDATE turn_traces SET closed_at='2026-08-17T00:00:00Z' WHERE turn_trace_id='turn-1'")
        connection.commit()
    report = assemble_episode(database, turn_trace_id="turn-1")
    assert report.status == "assembled"
    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT trace_state, coverage_state FROM episodes").fetchone() == ("incomplete", "partial")
