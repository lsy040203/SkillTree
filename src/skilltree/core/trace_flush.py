"""Single-writer, idempotent flush of bounded TraceEvents into SQLite."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from contextlib import closing

from skilltree.core.outbox import AtomicOutbox, WriterLease
from skilltree.core.trace_events import validate_trace_event
from skilltree.core.storage import Database
from skilltree.core.episode import assemble_episode


CLOSE_GRACE_SECONDS = 5


@dataclass(frozen=True)
class FlushReport:
    inserted: int = 0
    duplicates: int = 0
    quarantined: int = 0
    retained: int = 0


def flush_trace_events(database: Database, outbox: AtomicOutbox, lease: WriterLease, *, now: datetime) -> FlushReport:
    if not lease.acquire():
        return FlushReport(retained=len(list((outbox.root / "ready").glob("*.json"))))
    inserted = duplicates = quarantined = retained = 0
    quarantine = outbox.root / "quarantine"
    try:
        for path in sorted((outbox.root / "ready").glob("*.json")):
            try:
                event = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                retained += 1
                continue
            if not validate_trace_event(event):
                quarantine.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(quarantine / path.name))
                quarantined += 1
                continue
            try:
                with closing(database._connect()) as connection:  # single writer owns this transaction
                    connection.execute("BEGIN IMMEDIATE")
                    existing = connection.execute(
                        "SELECT payload_hash FROM trace_events WHERE event_id = ?", (event["event_id"],)
                    ).fetchone()
                    if existing is not None:
                        if existing[0] == event["payload_hash"]:
                            duplicates += 1
                            connection.commit()
                            path.unlink(missing_ok=True)
                            continue
                        connection.rollback()
                        quarantine.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(path), str(quarantine / path.name))
                        quarantined += 1
                        continue
                    sequence = connection.execute(
                        "SELECT COALESCE(MAX(ingest_sequence), 0) + 1 FROM trace_events WHERE turn_trace_id = ?",
                        (event["turn_trace_id"],),
                    ).fetchone()[0]
                    connection.execute(
                        "INSERT INTO trace_events(event_id, turn_trace_id, run_id, ingest_sequence, event_type, source, coverage_state, observed_at, payload_hash, payload_summary, tool_use_id, tool_name, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            event["event_id"], event["turn_trace_id"], event["run_id"] or None, sequence,
                            event["event_type"], event["source"], event["coverage_state"], event["observed_at"],
                            event["payload_hash"], event["payload_summary"], event["tool_use_id"], event["tool_name"],
                            event["observed_at"],
                        ),
                    )
                    if event["event_type"] == "run_closed":
                        connection.execute(
                            "UPDATE turn_traces SET closed_at = COALESCE(closed_at, ?) WHERE turn_trace_id = ?",
                            (event["observed_at"], event["turn_trace_id"]),
                        )
                    if str(event["source"]).startswith("hook:sha256:"):
                        hook_hash = str(event["source"])[5:]
                        connection.execute(
                            "INSERT INTO hook_observations(hook_bundle_hash, first_observed_at, last_observed_at, observed_count, last_event_id) "
                            "VALUES (?, ?, ?, 1, ?) ON CONFLICT(hook_bundle_hash) DO UPDATE SET "
                            "last_observed_at=excluded.last_observed_at, observed_count=hook_observations.observed_count + 1, last_event_id=excluded.last_event_id",
                            (hook_hash, event["observed_at"], event["observed_at"], event["event_id"]),
                        )
                    connection.commit()
                path.unlink(missing_ok=True)
                inserted += 1
            except Exception:
                retained += 1
        # Reconcile events written by an earlier runtime: `run_closed` is itself
        # authoritative persisted evidence, so this remains idempotent.
        with closing(database._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE turn_traces SET closed_at = ("
                "SELECT MIN(e.observed_at) FROM trace_events e "
                "WHERE e.turn_trace_id=turn_traces.turn_trace_id AND e.event_type='run_closed') "
                "WHERE closed_at IS NULL AND EXISTS (SELECT 1 FROM trace_events e "
                "WHERE e.turn_trace_id=turn_traces.turn_trace_id AND e.event_type='run_closed')"
            )
            connection.commit()
        with closing(database._connect()) as connection:
            closed_turns = list(connection.execute(
                "SELECT turn_trace_id, closed_at FROM turn_traces WHERE closed_at IS NOT NULL"
            ))
        grace_cutoff = now.astimezone(UTC) - timedelta(seconds=CLOSE_GRACE_SECONDS)
        for turn_trace_id, closed_at in closed_turns:
            try:
                closed_at_value = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00")).astimezone(UTC)
            except (TypeError, ValueError):
                continue
            if closed_at_value > grace_cutoff:
                # Stop can race with PostToolUse. Keep the trace open for a
                # bounded window so late terminal phases can be persisted
                # before Episode eligibility is evaluated.
                continue
            assemble_episode(database, turn_trace_id=turn_trace_id)
    finally:
        lease.release()
    return FlushReport(inserted=inserted, duplicates=duplicates, quarantined=quarantined, retained=retained)
