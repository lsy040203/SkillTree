"""Bounded, persistence-safe TraceEvent construction."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


EVENT_TYPES = {"tool_started", "tool_finished", "tool_failed", "run_closed", "user_feedback", "observation"}
COVERAGE_STATES = {"observed", "partial", "unobserved", "unattributed"}
TOOL_EVENT_TYPES = {"tool_started", "tool_finished", "tool_failed"}
MAX_SUMMARY_BYTES = 2048
FORBIDDEN_KEYS = {"prompt", "tool_input", "tool_response", "last_assistant_message", "transcript_path", "turn_token"}


def tool_calls_complete(events: Iterable[tuple[object, object]]) -> bool:
    """Return whether every observed tool start has exactly one terminal phase.

    A terminal phase is either ``tool_finished`` or ``tool_failed``.  Matching
    only by the host-provided ``tool_use_id`` keeps orphan events visible while
    preventing an unrelated completion from being treated as success.
    """
    starts: list[str] = []
    terminals: list[str] = []
    for event_type, tool_use_id in events:
        if event_type == "tool_started" and isinstance(tool_use_id, str):
            starts.append(tool_use_id)
        elif event_type in {"tool_finished", "tool_failed"} and isinstance(tool_use_id, str):
            terminals.append(tool_use_id)
    # A tool_use_id identifies one invocation.  Reject duplicate starts or
    # terminal phases instead of accepting equal list lengths by coincidence.
    # Set equality preserves delivery-order independence while the length
    # checks preserve the one-start/one-terminal invariant.
    return (
        len(starts) == len(set(starts))
        and len(terminals) == len(set(terminals))
        and set(starts) == set(terminals)
    )


def build_trace_event(
    *,
    event_id: str,
    turn_trace_id: str,
    run_id: str | None,
    event_type: str,
    source: str,
    coverage_state: str,
    observed_at: str,
    payload_summary: str,
    payload_hash: str,
    tool_use_id: str | None = None,
    tool_name: str | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {
        "event_id": event_id,
        "turn_trace_id": turn_trace_id,
        "run_id": run_id,
        "event_type": event_type,
        "source": source,
        "coverage_state": coverage_state,
        "observed_at": observed_at,
        "payload_hash": payload_hash,
        "payload_summary": payload_summary,
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
    }
    if event_type not in EVENT_TYPES:
        raise ValueError("event_type_invalid")
    if coverage_state not in COVERAGE_STATES:
        raise ValueError("coverage_state_invalid")
    for key in ("event_id", "turn_trace_id", "source", "observed_at", "payload_hash", "payload_summary"):
        if not isinstance(values[key], str) or not values[key]:
            raise ValueError("trace_event_field_invalid")
    if run_id is not None and (not isinstance(run_id, str) or not run_id):
        raise ValueError("run_id_invalid")
    if not isinstance(payload_summary, str) or len(payload_summary.encode("utf-8")) > MAX_SUMMARY_BYTES:
        raise ValueError("payload_summary_too_large")
    if event_type in TOOL_EVENT_TYPES:
        if not isinstance(tool_use_id, str) or not tool_use_id or not isinstance(tool_name, str) or not tool_name:
            raise ValueError("tool_identity_required")
    elif tool_use_id is not None or tool_name is not None:
        raise ValueError("tool_identity_unexpected")
    return values


def validate_trace_event(value: object) -> bool:
    if not isinstance(value, dict) or FORBIDDEN_KEYS.intersection(value):
        return False
    allowed = {
        "event_id", "turn_trace_id", "run_id", "event_type", "source", "coverage_state",
        "observed_at", "payload_hash", "payload_summary", "tool_use_id", "tool_name",
    }
    if set(value) != allowed:
        return False
    try:
        build_trace_event(**value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return True
