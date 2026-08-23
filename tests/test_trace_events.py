from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from skilltree.core.outbox import AtomicOutbox
from skilltree.core.trace_events import build_trace_event, tool_calls_complete, validate_trace_event


BASE = {
    "event_id": "event-1",
    "turn_trace_id": "turn-1",
    "run_id": "run-1",
    "event_type": "tool_started",
    "source": "hook",
    "coverage_state": "observed",
    "observed_at": "2026-08-17T00:00:00Z",
    "payload_summary": "tool started",
    "payload_hash": "sha256:" + "a" * 64,
    "tool_use_id": "tool-1",
    "tool_name": "bash",
}


def test_build_trace_event_is_bounded_and_rejects_sensitive_fields() -> None:
    event = build_trace_event(**BASE)
    assert set(event) == set(BASE)
    assert validate_trace_event(event)
    with pytest.raises(ValueError, match="payload_summary_too_large"):
        build_trace_event(**{**BASE, "payload_summary": "x" * 2049})
    assert not validate_trace_event({**event, "tool_input": {"secret": "raw"}})


def test_tool_event_requires_identity_and_non_tool_event_does_not_allow_it() -> None:
    with pytest.raises(ValueError, match="tool_identity_required"):
        build_trace_event(**{**BASE, "tool_use_id": None})
    with pytest.raises(ValueError, match="tool_identity_unexpected"):
        build_trace_event(**{**BASE, "event_type": "run_closed", "tool_use_id": None, "tool_name": "bash"})


def test_user_feedback_is_a_valid_non_tool_trace_event() -> None:
    event = build_trace_event(**{
        **BASE,
        "event_id": "feedback-1",
        "event_type": "user_feedback",
        "payload_summary": "accepted",
        "tool_use_id": None,
        "tool_name": None,
    })

    assert validate_trace_event(event)


def test_trace_event_outbox_is_atomic() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = AtomicOutbox(Path(temp_dir) / "outbox").enqueue_trace_event(BASE)
        assert path.exists()
        assert not list(path.parent.parent.joinpath("staging").glob("*"))


def test_tool_calls_complete_allows_delivery_reordering_but_rejects_duplicate_phases() -> None:
    # Persistence order is not causal: a terminal event may be flushed before
    # its start.  The trusted tool_use_id still lets the pair converge.
    assert tool_calls_complete([
        ("tool_finished", "tool-1"),
        ("tool_started", "tool-1"),
    ])

    # Matching counts alone are insufficient: two starts and two terminals do
    # not describe one invocation and must remain incomplete.
    assert not tool_calls_complete([
        ("tool_started", "tool-1"),
        ("tool_started", "tool-1"),
        ("tool_finished", "tool-1"),
        ("tool_finished", "tool-1"),
    ])
