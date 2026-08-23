"""Closure and conservative Episode assembly over persisted TraceEvents."""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from skilltree.core.storage import Database
from skilltree.core.trace_events import build_trace_event, tool_calls_complete


@dataclass(frozen=True)
class EpisodeReport:
    status: str
    episode_id: str | None = None


def close_turn_trace(database: Database, *, turn_trace_id: str, observed_at: str) -> bool:
    """Close exactly once; a model RouteDecision is deliberately irrelevant here."""
    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT closed_at FROM turn_traces WHERE turn_trace_id = ?", (turn_trace_id,)).fetchone()
        if row is None:
            connection.rollback()
            return False
        if row[0] is not None:
            connection.commit()
            return True
        connection.execute("UPDATE turn_traces SET closed_at = ? WHERE turn_trace_id = ?", (observed_at, turn_trace_id))
        connection.commit()
    return True


def record_outcome_assessment(
    database: Database, *, run_id: str, turn_trace_id: str, event_id: str,
    source: str, verdict: str, outcome_summary: str, observed_at: str,
    evidence_ref: str | None = None,
) -> str:
    """Persist only an explicit bounded assessment; never infer it from model text."""
    if source not in {"user", "read_only_verifier", "tool_adapter"} or verdict not in {"success", "failed", "cancelled", "unknown"}:
        raise ValueError("outcome_invalid")
    if not outcome_summary or len(outcome_summary.encode("utf-8")) > 2048:
        raise ValueError("outcome_summary_invalid")
    assessment_id = str(uuid4())
    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        binding = connection.execute("SELECT 1 FROM run_turn_bindings WHERE run_id=? AND turn_trace_id=?", (run_id, turn_trace_id)).fetchone()
        if binding is None:
            connection.rollback()
            raise ValueError("outcome_unattributed")
        existing = connection.execute("SELECT assessment_id FROM outcome_assessments WHERE event_id=?", (event_id,)).fetchone()
        if existing is not None:
            assessment_id = existing[0]
        else:
            connection.execute(
                "INSERT INTO outcome_assessments(assessment_id,run_id,turn_trace_id,event_id,source,verdict,outcome_summary,evidence_ref,observed_at,supersedes_event_id) VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                (assessment_id, run_id, turn_trace_id, event_id, source, verdict, outcome_summary, evidence_ref, observed_at),
            )
        latest = connection.execute(
            "SELECT assessment_id, verdict FROM outcome_assessments "
            "WHERE run_id=? ORDER BY observed_at DESC, rowid DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if latest is not None:
            connection.execute(
                "UPDATE episodes SET verdict=?, outcome_ref=? WHERE run_id=?",
                (latest[1], latest[0], run_id),
            )
        connection.commit()
    return assessment_id


def assemble_episode(database: Database, *, turn_trace_id: str) -> EpisodeReport:
    """Create an Episode only from closed, fully observed persisted records."""
    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT t.closed_at, t.coverage_state, t.prompt_hash, r.run_id, r.snapshot_json "
            "FROM turn_traces t JOIN run_turn_bindings b ON b.turn_trace_id=t.turn_trace_id "
            "JOIN run_contexts r ON r.run_id=b.run_id WHERE t.turn_trace_id=?", (turn_trace_id,)
        ).fetchone()
        if row is None or row[0] is None:
            connection.rollback()
            return EpisodeReport("trace_incomplete")
        closed_at, coverage_state, objective_hash, run_id, snapshot_json = row
        events = connection.execute(
            "SELECT event_type, coverage_state, tool_use_id FROM trace_events WHERE turn_trace_id=? ORDER BY ingest_sequence", (turn_trace_id,)
        ).fetchall()
        complete_trace = (
            bool(events)
            and all(item[1] == "observed" for item in events)
            and any(item[0] == "run_closed" for item in events)
            and tool_calls_complete((item[0], item[2]) for item in events)
        )
        coverage = "observed" if complete_trace else "partial"
        assessment = connection.execute(
            "SELECT verdict, assessment_id FROM outcome_assessments WHERE run_id=? ORDER BY observed_at DESC LIMIT 1", (run_id,)
        ).fetchone()
        verdict, outcome_ref = assessment if assessment else ("unknown", None)
        existing = connection.execute(
            "SELECT episode_id, trace_state FROM episodes WHERE run_id = ?", (run_id,)
        ).fetchone()
        episode_id = str(uuid4())
        retention_until = (datetime.now(UTC) + timedelta(days=90)).isoformat(timespec="seconds").replace("+00:00", "Z")
        snapshot_partial = int(snapshot_json in {"", "{}", "null", "[]"})
        trace_state = "complete" if complete_trace and not snapshot_partial else "incomplete"
        if existing is None:
            connection.execute(
                "INSERT INTO episodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (episode_id, run_id, turn_trace_id, objective_hash, "[redacted]", snapshot_json, snapshot_partial, trace_state, coverage, verdict, len(events), outcome_ref, closed_at, retention_until),
            )
        elif existing[1] != "complete" and trace_state == "complete":
            episode_id = existing[0]
            connection.execute(
                "UPDATE episodes SET snapshot_partial=?, trace_state=?, coverage_state=?, verdict=?, event_count=?, outcome_ref=? WHERE episode_id=?",
                (snapshot_partial, trace_state, coverage, verdict, len(events), outcome_ref, episode_id),
            )
        else:
            episode_id = existing[0]
        connection.commit()
    return EpisodeReport("assembled", episode_id)
