"""Read-only normalized trajectory projection."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing

from skilltree.core.storage import Database
from skilltree.core.trace_events import tool_calls_complete


@dataclass(frozen=True)
class NormalizedRecord:
    event_id: str
    event_type: str
    ingest_sequence: int
    coverage_state: str
    payload_summary: str


@dataclass(frozen=True)
class NormalizedTurn:
    turn_trace_id: str
    trace_state: str
    coverage_state: str
    verdict: str
    records: tuple[NormalizedRecord, ...]


@dataclass(frozen=True)
class NormalizedSession:
    session_id: str
    turns: tuple[NormalizedTurn, ...]


def read_turn_trajectory(database: Database, turn_trace_id: str) -> NormalizedTurn | None:
    with closing(database._connect()) as connection:
        turn = connection.execute("SELECT closed_at, coverage_state FROM turn_traces WHERE turn_trace_id=?", (turn_trace_id,)).fetchone()
        if turn is None:
            return None
        rows = connection.execute("SELECT event_id,event_type,ingest_sequence,coverage_state,payload_summary,tool_use_id FROM trace_events WHERE turn_trace_id=? ORDER BY ingest_sequence", (turn_trace_id,)).fetchall()
        verdict_row = connection.execute("SELECT oa.verdict FROM outcome_assessments oa JOIN run_turn_bindings b ON b.run_id=oa.run_id WHERE b.turn_trace_id=? ORDER BY oa.observed_at DESC LIMIT 1", (turn_trace_id,)).fetchone()
    records = tuple(NormalizedRecord(*row[:5]) for row in rows)
    complete = (
        turn[0] is not None
        and bool(records)
        and any(row.event_type == "run_closed" for row in records)
        and all(row.coverage_state == "observed" for row in records)
        and tool_calls_complete((row[1], row[5]) for row in rows)
    )
    return NormalizedTurn(turn_trace_id, "complete" if complete else "incomplete", turn[1], verdict_row[0] if verdict_row else "unknown", records)


def read_session_trajectory(database: Database, session_id: str) -> NormalizedSession | None:
    """Return a read-only aggregate of persisted turns for one session."""
    with closing(database._connect()) as connection:
        rows = connection.execute(
            "SELECT turn_trace_id FROM turn_traces WHERE session_id=? ORDER BY rowid", (session_id,)
        ).fetchall()
    if not rows:
        return None
    turns = tuple(
        turn for (turn_trace_id,) in rows
        if (turn := read_turn_trajectory(database, turn_trace_id)) is not None
    )
    return NormalizedSession(session_id, turns)
