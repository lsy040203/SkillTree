from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from skilltree.bundle import build_bundle
from skilltree.core.learning import (
    apply_explicit_feedback,
    apply_outcome_assessment,
    decay_weights,
    list_weights,
    rebuild_weights,
)
from skilltree.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"
WORKSPACE = "sha256:" + "a" * 64


def _database(tmp_path: Path) -> Database:
    build_bundle(ROOT)
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=7)
    return database


def _weight(database: Database, name: str) -> int:
    return next((item["weight"] for item in list_weights(database, workspace_id=WORKSPACE) if item["skill_name"] == name), 0)


def test_explicit_feedback_is_idempotent_and_audited(tmp_path: Path) -> None:
    database = _database(tmp_path)
    first = apply_explicit_feedback(database, workspace_id=WORKSPACE, skill_names=["analyze"], action="select", evidence_handle="feedback-1", occurred_at="2026-01-01T00:00:00Z")
    second = apply_explicit_feedback(database, workspace_id=WORKSPACE, skill_names=["analyze"], action="select", evidence_handle="feedback-1", occurred_at="2026-01-01T00:00:00Z")
    assert first["changed"] is True
    assert second["changed"] is False
    assert _weight(database, "analyze") == 2
    with closing(sqlite3.connect(database.path)) as connection:
        assert connection.execute("SELECT old_weight,new_weight,delta,evidence_handle,rule_version FROM skill_weight_updates").fetchone() == (0, 2, 2, "feedback-1", "learning/v1")


def test_switch_updates_old_and_new_and_clips_bounds(tmp_path: Path) -> None:
    database = _database(tmp_path)
    for index in range(6):
        apply_explicit_feedback(database, workspace_id=WORKSPACE, skill_names=["old"], action="select", evidence_handle=f"old-{index}")
    for index in range(6):
        apply_explicit_feedback(database, workspace_id=WORKSPACE, skill_names=["new"], action="reject", evidence_handle=f"new-{index}")
    apply_explicit_feedback(database, workspace_id=WORKSPACE, skill_names=["old", "new"], action="switch", evidence_handle="switch-1", occurred_at="2026-01-01T00:00:00Z")
    assert _weight(database, "old") == 8
    assert _weight(database, "new") == -8


def test_outcome_updates_distinct_executed_skills_and_relaxed_fallback(tmp_path: Path) -> None:
    database = _database(tmp_path)
    result = apply_outcome_assessment(database, workspace_id=WORKSPACE, assessment_handle="assessment-1", verdict="success", coverage_state="observed", executed_skills=["analyze", "code-review", "analyze"])
    assert {item["skill_name"] for item in result["updates"]} == {"analyze", "code-review"}
    assert _weight(database, "analyze") == 1
    assert _weight(database, "code-review") == 1

    relaxed = apply_outcome_assessment(database, workspace_id=WORKSPACE, assessment_handle="assessment-2", verdict="success", coverage_state="unobserved", selected_skill="lsp")
    assert relaxed["updates"][0]["evidence_quality"] == "relaxed"
    assert _weight(database, "lsp") == 1


def test_failed_outcome_only_penalizes_direct_failure_skills(tmp_path: Path) -> None:
    database = _database(tmp_path)
    apply_outcome_assessment(database, workspace_id=WORKSPACE, assessment_handle="assessment-1", verdict="failed", coverage_state="observed", executed_skills=["analyze", "code-review"], failed_skills=["code-review"])
    assert _weight(database, "code-review") == -1
    assert _weight(database, "analyze") == 0


def test_decay_uses_complete_30_day_periods_and_rebuild_ignores_decay_rows(tmp_path: Path) -> None:
    database = _database(tmp_path)
    apply_explicit_feedback(database, workspace_id=WORKSPACE, skill_names=["analyze"], action="select", evidence_handle="feedback-1", occurred_at="2026-01-01T00:00:00Z")
    assert _weight(database, "analyze") == 2
    assert decay_weights(database, workspace_id=WORKSPACE, as_of="2026-01-29T00:00:00Z")["changed"] is False
    assert _weight(database, "analyze") == 2
    assert len(decay_weights(database, workspace_id=WORKSPACE, as_of="2026-03-02T00:00:00Z")["updates"]) == 2
    assert _weight(database, "analyze") == 0
    rebuilt = rebuild_weights(database, workspace_id=WORKSPACE, as_of="2026-03-02T00:00:00Z")
    assert _weight(database, "analyze") == 0
    assert rebuilt["weights"][0]["weight"] == 0
