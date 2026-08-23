from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from skilltree.bundle import build_bundle
from skilltree.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"
NOW = "2026-08-19T00:00:00Z"
HASH = "sha256:" + "a" * 64


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
            "INSERT INTO turn_traces VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("turn-1", "session-1", "turn-1", HASH, "workspace-1", HASH, NOW, NOW, NOW, HASH, "observed", NOW, "2026-11-17T00:00:00Z"),
        )
        connection.execute("INSERT INTO run_turn_bindings VALUES (?,?,?,?)", ("run-1", "turn-1", NOW, "normal"))
        for sequence, event_id, event_type, summary in (
            (1, "event-1", "tool_started", "inspect structure"),
            (2, "event-2", "tool_finished", "read docs"),
        ):
            connection.execute(
                "INSERT INTO trace_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event_id, "turn-1", "run-1", sequence, event_type, "hook", "observed", NOW, HASH, summary, "tool-1", "shell", NOW),
            )
        connection.execute(
            "INSERT INTO outcome_assessments VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("assessment-1", "run-1", "turn-1", "outcome-event", "read_only_verifier", "success", "verified", None, NOW, None),
        )
        connection.commit()
    return database


def test_bundle_preserves_route_trace_and_successful_execution(tmp_path: Path) -> None:
    from skilltree.core.evidence import build_evidence_bundle

    bundle = build_evidence_bundle(_database(tmp_path), run_id="run-1")

    assert bundle is not None
    assert bundle.recommended_skills == ("analyze",)
    assert bundle.observed_tool_steps == ("inspect structure",)
    assert bundle.outcome == "success"
    assert bundle.outcome_evidence_kind == "successful_execution"
    assert bundle.evidence_event_ids == ("event-1", "event-2")
    assert bundle.route_degraded is False
    assert bundle.observed_tool_chain == (
        {
            "tool_use_id": "tool-1",
            "tool_name": "shell",
            "started_event_id": "event-1",
            "finished_event_id": "event-2",
            "failed_event_id": None,
            "status": "finished",
            "summaries": ("inspect structure", "read docs"),
        },
    )


def test_bundle_projects_one_step_per_tool_invocation(tmp_path: Path) -> None:
    from skilltree.core.evidence import build_evidence_bundle

    bundle = build_evidence_bundle(_database(tmp_path), run_id="run-1")

    assert bundle is not None
    assert bundle.observed_tool_steps == ("inspect structure",)


def test_bundle_marks_duplicate_tool_phases_and_preserves_all_event_handles(tmp_path: Path) -> None:
    from skilltree.core.evidence import build_evidence_bundle

    database = _database(tmp_path)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "INSERT INTO trace_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("event-3", "turn-1", "run-1", 3, "tool_started", "hook", "observed", NOW, HASH,
             "duplicate start", "tool-1", "shell", NOW),
        )
        connection.commit()

    bundle = build_evidence_bundle(database, run_id="run-1")

    assert bundle is not None
    assert bundle.coverage_state == "partial"
    assert bundle.observed_tool_chain[0]["status"] == "invalid_duplicate"
    assert bundle.observed_tool_chain[0]["started_event_ids"] == ("event-1", "event-3")


def test_bundle_preserves_degraded_route_flag(tmp_path: Path) -> None:
    from skilltree.core.evidence import build_evidence_bundle

    database = _database(tmp_path)
    with closing(sqlite3.connect(database.path)) as connection:
        decision = json.loads(connection.execute(
            "SELECT decision_json FROM route_decisions WHERE run_id='run-1'"
        ).fetchone()[0])
        decision["degraded"] = True
        connection.execute(
            "UPDATE route_decisions SET decision_json=? WHERE run_id='run-1'",
            (json.dumps(decision),),
        )
        connection.commit()

    bundle = build_evidence_bundle(database, run_id="run-1")

    assert bundle is not None
    assert bundle.route_degraded is True


def test_bundle_returns_none_when_memory_write_consent_is_disabled(tmp_path: Path) -> None:
    from skilltree.core.evidence import build_evidence_bundle

    database = _database(tmp_path)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("UPDATE runtime_config SET memory_write_enabled=0")
        connection.commit()

    assert build_evidence_bundle(database, run_id="run-1") is None


def test_bundle_marks_orphan_tool_phase_as_partial(tmp_path: Path) -> None:
    from skilltree.core.evidence import build_evidence_bundle

    database = _database(tmp_path)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "UPDATE trace_events SET tool_use_id='tool-start' WHERE event_id='event-1'"
        )
        connection.execute(
            "UPDATE trace_events SET tool_use_id='tool-finish' WHERE event_id='event-2'"
        )
        connection.commit()

    bundle = build_evidence_bundle(database, run_id="run-1")

    assert bundle is not None
    assert bundle.coverage_state == "partial"
    assert bundle.outcome_evidence_kind == "none"


def test_bundle_derives_semantic_verification_context_from_observed_categories(tmp_path: Path) -> None:
    from skilltree.core.evidence import build_evidence_bundle

    database = _database(tmp_path)
    with closing(sqlite3.connect(database.path)) as connection:
        decision = json.loads(connection.execute(
            "SELECT decision_json FROM route_decisions WHERE run_id='run-1'"
        ).fetchone()[0])
        decision["intent"] = {"name": "skill_routing", "confidence": 1.0}
        connection.execute(
            "UPDATE route_decisions SET decision_json=? WHERE run_id='run-1'",
            (json.dumps(decision),),
        )
        connection.execute(
            "UPDATE trace_events SET payload_summary='PreToolUse:Bash:read_source' WHERE event_id='event-1'"
        )
        connection.execute(
            "UPDATE trace_events SET payload_summary='PreToolUse:Bash:run_python' WHERE event_id='event-2'"
        )
        connection.commit()

    bundle = build_evidence_bundle(database, run_id="run-1")

    assert bundle is not None
    assert bundle.task_type == "repository_verification"
    assert bundle.scenario_key == "read_and_execute_verification"
    assert bundle.scenario_label == "读取源码并执行验证"
