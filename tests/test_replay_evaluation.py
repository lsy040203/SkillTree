from __future__ import annotations

import pytest
import sqlite3
from pathlib import Path
from uuid import uuid4

from skilltree.bundle import build_bundle
from skilltree.core.storage import Database
from skilltree.core.replay_evaluation import ReplayEvaluationError, compare_arms, create_evolution_candidate, persist_replay_report, run_evolve_scan, transition_candidate

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


def _arm(episode_id: str, verdict: str, quality: float) -> dict[str, object]:
    return {"episode_id": episode_id, "verdict": verdict, "quality_score": quality, "latency_ms": 10, "guardrail_breaches": []}


def test_report_requires_paired_arms_and_only_marks_improvement_with_coverage() -> None:
    report = compare_arms(report_id="r", candidate_id="c", episode_ids=["e1", "e2", "e3"], baseline=[_arm("e1", "success", .4), _arm("e2", "failed", .2), _arm("e3", "failed", .1)], candidate=[_arm("e1", "success", .8), _arm("e2", "success", .7), _arm("e3", "failed", .2)], created_at="2026-08-22T00:00:00Z")
    assert report.verdict == "improved"
    assert report.coverage["complete"] is True


def test_guardrail_breach_is_insufficient_and_illegal_state_is_rejected() -> None:
    arm = _arm("e1", "success", .9); arm["guardrail_breaches"] = ["network_denied"]
    report = compare_arms(report_id="r", candidate_id="c", episode_ids=["e1"], baseline=[_arm("e1", "success", .4)], candidate=[arm], created_at="now")
    assert report.verdict == "insufficient"
    with pytest.raises(ReplayEvaluationError, match="invalid_transition"):
        transition_candidate("replay_passed", "draft")


def test_candidate_and_report_are_persisted_atomically(tmp_path: Path) -> None:
    build_bundle(ROOT)
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=8)
    run_id, trace_id, episode_id = (str(uuid4()) for _ in range(3))
    capsule_id, candidate_id = str(uuid4()), str(uuid4())
    with sqlite3.connect(database.path) as connection:
        connection.execute("INSERT INTO run_contexts(run_id,workspace_id,user_id,snapshot_json,trace_capture_enabled,memory_read_enabled,memory_write_enabled,replay_capture_enabled,created_at,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?)", (run_id, "w", "local", "[]", 1, 1, 1, 1, "2026-08-22T00:00:00Z", "2027-01-01T00:00:00Z"))
        connection.execute("INSERT INTO turn_traces(turn_trace_id,session_id,turn_id,session_id_hash,workspace_id,turn_token_hash,soft_expires_at,hard_expires_at,prompt_hash,coverage_state,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (trace_id, "s", "t", "sha256:" + "a" * 64, "w", "sha256:" + "b" * 64, "2026-08-23T00:00:00Z", "2026-08-24T00:00:00Z", "sha256:" + "c" * 64, "observed", "2027-01-01T00:00:00Z"))
        connection.execute("INSERT INTO episodes(episode_id,run_id,turn_trace_id,objective_hash,objective_preview,trusted_skill_snapshot,snapshot_partial,trace_state,coverage_state,verdict,event_count,outcome_ref,created_at,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (episode_id, run_id, trace_id, "sha256:" + "d" * 64, "[redacted]", "[]", 0, "complete", "observed", "success", 1, None, "2026-08-22T00:00:00Z", "2027-01-01T00:00:00Z"))
        connection.execute("INSERT INTO replay_capsules(replay_capsule_id,run_id,workspace_id,mode,consent_id,blob_handle,content_hash,status,expires_at,retention_until,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (capsule_id, run_id, "w", "fixture_only", str(uuid4()), "x", "sha256:" + "e" * 64, "ready", "2026-12-01T00:00:00Z", "2027-01-01T00:00:00Z", "2026-08-22T00:00:00Z"))
    created = create_evolution_candidate(database, candidate_id=candidate_id, workspace_id="w", episode_ids=[episode_id], now="2026-08-22T00:00:00Z")
    assert created["status"] == "draft"
    report = compare_arms(report_id=str(uuid4()), candidate_id=candidate_id, episode_ids=[episode_id], baseline=[_arm(episode_id, "success", .2)], candidate=[_arm(episode_id, "success", .8)], created_at="2026-08-22T00:00:00Z")
    persisted = persist_replay_report(database, report)
    assert persisted["status"] == "draft"
    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT status FROM evolution_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()[0] == "draft"


def test_evolve_scan_runs_two_arms_and_persists_improved_candidate(tmp_path: Path, monkeypatch) -> None:
    build_bundle(ROOT)
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=8)
    ids = [str(uuid4()) for _ in range(3)]
    runs = [str(uuid4()) for _ in range(3)]
    traces = [str(uuid4()) for _ in range(3)]
    candidate_id = str(uuid4())
    with sqlite3.connect(database.path) as connection:
        for index, (episode_id, run_id, trace_id) in enumerate(zip(ids, runs, traces, strict=True)):
            connection.execute("INSERT INTO run_contexts(run_id,workspace_id,user_id,snapshot_json,trace_capture_enabled,memory_read_enabled,memory_write_enabled,replay_capture_enabled,created_at,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?)", (run_id, "w", "local", "[]", 1, 1, 1, 1, "2026-08-22T00:00:00Z", "2027-01-01T00:00:00Z"))
            connection.execute("INSERT INTO turn_traces(turn_trace_id,session_id,turn_id,session_id_hash,workspace_id,turn_token_hash,soft_expires_at,hard_expires_at,prompt_hash,coverage_state,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (trace_id, "s" + str(index), "t", "sha256:" + str(index) * 64, "w", "sha256:" + str(index + 3) * 64, "2026-08-23T00:00:00Z", "2026-08-24T00:00:00Z", "sha256:" + str(index + 4) * 64, "observed", "2027-01-01T00:00:00Z"))
            connection.execute("INSERT INTO episodes(episode_id,run_id,turn_trace_id,objective_hash,objective_preview,trusted_skill_snapshot,snapshot_partial,trace_state,coverage_state,verdict,event_count,outcome_ref,created_at,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (episode_id, run_id, trace_id, "sha256:" + "a" * 64, "[redacted]", "[]", 0, "complete", "observed", "success", 1, None, "2026-08-22T00:00:00Z", "2027-01-01T00:00:00Z"))
            connection.execute("INSERT INTO replay_capsules(replay_capsule_id,run_id,workspace_id,mode,consent_id,blob_handle,content_hash,status,expires_at,retention_until,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (str(uuid4()), run_id, "w", "fixture_only", str(uuid4()), "x", "sha256:" + "b" * 64, "ready", "2026-12-01T00:00:00Z", "2027-01-01T00:00:00Z", "2026-08-22T00:00:00Z"))
    monkeypatch.setattr("skilltree.core.replay_evaluation.read_replay_capsule", lambda *_: {"fixture": {"ok": True}})
    def fake_arm(*, arm, episode_id, **kwargs):
        baseline_verdict = {ids[0]: "success", ids[1]: "failed", ids[2]: "failed"}[episode_id]
        verdict = baseline_verdict if arm == "baseline" else ("success" if episode_id != ids[2] else "failed")
        return _arm(episode_id, verdict, .2 if arm == "baseline" else .8)
    result = run_evolve_scan(database, data_dir=tmp_path, workspace_id="w", candidate_id=candidate_id, episode_ids=ids, runtime_state={"schema_version": "skilltree-replay-runtime/v1", "image_name": "runner", "image_digest": "sha256:" + "a" * 64}, docker_path=tmp_path / "docker.exe", arm_runner=fake_arm)
    assert result["status"] == "replay_passed"


def test_evolve_scan_projects_arm_and_python_fixture_into_container_input(tmp_path: Path, monkeypatch) -> None:
    build_bundle(ROOT)
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=8)
    episode_id, run_id, trace_id, capsule_id, candidate_id = (str(uuid4()) for _ in range(5))
    with sqlite3.connect(database.path) as connection:
        connection.execute("INSERT INTO run_contexts(run_id,workspace_id,user_id,snapshot_json,trace_capture_enabled,memory_read_enabled,memory_write_enabled,replay_capture_enabled,created_at,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?)", (run_id, "w", "local", "[]", 1, 1, 1, 1, "2026-08-22T00:00:00Z", "2027-01-01T00:00:00Z"))
        connection.execute("INSERT INTO turn_traces(turn_trace_id,session_id,turn_id,session_id_hash,workspace_id,turn_token_hash,soft_expires_at,hard_expires_at,prompt_hash,coverage_state,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (trace_id, "s", "t", "sha256:" + "a" * 64, "w", "sha256:" + "b" * 64, "2026-08-23T00:00:00Z", "2026-08-24T00:00:00Z", "sha256:" + "c" * 64, "observed", "2027-01-01T00:00:00Z"))
        connection.execute("INSERT INTO episodes(episode_id,run_id,turn_trace_id,objective_hash,objective_preview,trusted_skill_snapshot,snapshot_partial,trace_state,coverage_state,verdict,event_count,outcome_ref,created_at,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (episode_id, run_id, trace_id, "sha256:" + "d" * 64, "[redacted]", "[]", 0, "complete", "observed", "success", 1, None, "2026-08-22T00:00:00Z", "2027-01-01T00:00:00Z"))
        connection.execute("INSERT INTO replay_capsules(replay_capsule_id,run_id,workspace_id,mode,consent_id,blob_handle,content_hash,status,expires_at,retention_until,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (capsule_id, run_id, "w", "fixture_only", str(uuid4()), "x", "sha256:" + "e" * 64, "ready", "2026-12-01T00:00:00Z", "2027-01-01T00:00:00Z", "2026-08-22T00:00:00Z"))
    monkeypatch.setattr("skilltree.core.replay_evaluation.read_replay_capsule", lambda *_: {"fixture": {"source_name": "lesson.py", "source": "class Solution: pass"}})
    observed: list[tuple[str, str]] = []
    def fake_arm(*, arm, input_dir, **kwargs):
        observed.append((arm, (input_dir / "arm.txt").read_text(encoding="utf-8")))
        assert (input_dir / "lesson.py").read_text(encoding="utf-8") == "class Solution: pass"
        return _arm(episode_id, "success", .5)
    result = run_evolve_scan(database, data_dir=tmp_path, workspace_id="w", candidate_id=candidate_id, episode_ids=[episode_id], runtime_state={"schema_version": "skilltree-replay-runtime/v1", "image_name": "runner", "image_digest": "sha256:" + "a" * 64}, docker_path=tmp_path / "docker.exe", arm_runner=fake_arm)
    assert result["status"] == "draft"
    assert observed == [("baseline", "baseline"), ("candidate", "candidate")]
