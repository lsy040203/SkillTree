"""Deterministic baseline/candidate comparison and P6 state transitions."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import tempfile
from pathlib import Path
from contextlib import closing
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from skilltree.core.replay_capsules import read_replay_capsule
from skilltree.core.replay_runner import run_arm
from skilltree.core.extension_registry import ExtensionRegistryError, resolve_task_type
from dataclasses import dataclass
from typing import Literal


Arm = dict[str, object]
CandidateStatus = Literal["draft", "replay_passed", "rejected", "rolled_back"]


class ReplayEvaluationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def create_evolution_candidate(database, *, candidate_id: str, workspace_id: str, episode_ids: list[str], now: str | None = None) -> dict[str, object]:
    """Persist a draft candidate and immutable Episode references atomically."""
    _uuid(candidate_id)
    if not workspace_id or not 1 <= len(episode_ids) <= 20 or len(set(episode_ids)) != len(episode_ids):
        raise ReplayEvaluationError("invalid_schema")
    for item in episode_ids:
        _uuid(item)
    created = now or datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    retention = (datetime.fromisoformat(created.replace("Z", "+00:00")) + timedelta(days=90)).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        with closing(database._connect()) as connection:
            existing = connection.execute("SELECT workspace_id,status FROM evolution_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            if existing:
                if existing[0] != workspace_id:
                    raise ReplayEvaluationError("out_of_scope")
                return {"candidate_id": candidate_id, "status": existing[1], "episode_ids": episode_ids}
            placeholders = ",".join("?" for _ in episode_ids)
            rows = connection.execute(
                f"SELECT e.episode_id FROM episodes e JOIN run_contexts r ON r.run_id=e.run_id "
                f"JOIN replay_capsules c ON c.run_id=e.run_id "
                f"WHERE r.workspace_id=? AND e.trace_state='complete' AND e.coverage_state='observed' AND e.snapshot_partial=0 AND c.status='ready' AND e.episode_id IN ({placeholders})",
                (workspace_id, *episode_ids),
            ).fetchall()
            if len(rows) != len(episode_ids):
                raise ReplayEvaluationError("out_of_scope")
            connection.execute("INSERT INTO evolution_candidates(candidate_id,workspace_id,status,created_at,retention_until) VALUES (?,?,?,?,?)", (candidate_id, workspace_id, "draft", created, retention))
            connection.executemany("INSERT INTO evolution_candidate_episode_refs(candidate_id,episode_id) VALUES (?,?)", [(candidate_id, item) for item in episode_ids])
            connection.commit()
    except ReplayEvaluationError:
        raise
    except sqlite3.Error as error:
        raise ReplayEvaluationError("internal_error") from error
    return {"candidate_id": candidate_id, "status": "draft", "episode_ids": episode_ids}


def persist_replay_report(database, report: "ReplayReport", *, retention_days: int = 90) -> dict[str, object]:
    """Persist metadata-only report and migrate its draft candidate on improvement."""
    if report.verdict == "insufficient" or report.guardrail_breaches:
        raise ReplayEvaluationError("guardrail_breached")
    target = "replay_passed" if report.verdict == "improved" else "draft"
    now = report.created_at
    try:
        with closing(database._connect()) as connection:
            row = connection.execute("SELECT status FROM evolution_candidates WHERE candidate_id=?", (report.candidate_id,)).fetchone()
            if row is None:
                raise ReplayEvaluationError("not_found")
            if row[0] != "draft":
                raise ReplayEvaluationError("invalid_transition")
            retention = (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(days=retention_days)).isoformat(timespec="seconds").replace("+00:00", "Z")
            connection.execute("INSERT INTO replay_reports(report_id,candidate_id,created_at,retention_until) VALUES (?,?,?,?)", (report.report_id, report.candidate_id, now, retention))
            connection.execute("UPDATE evolution_candidates SET status=? WHERE candidate_id=?", (target, report.candidate_id))
            connection.commit()
    except ReplayEvaluationError:
        raise
    except sqlite3.IntegrityError as error:
        raise ReplayEvaluationError("conflict") from error
    except sqlite3.Error as error:
        raise ReplayEvaluationError("internal_error") from error
    return {"report_id": report.report_id, "candidate_id": report.candidate_id, "status": target, "verdict": report.verdict}


def run_evolve_scan(database, *, data_dir: Path, workspace_id: str, candidate_id: str, episode_ids: list[str], runtime_state: dict[str, str], docker_path: Path, arm_runner=run_arm) -> dict[str, object]:
    """Run one explicit baseline/candidate pass over the same authorized Capsules."""
    _uuid(candidate_id)
    if not episode_ids:
        raise ReplayEvaluationError("invalid_schema")
    try:
        create_evolution_candidate(database, candidate_id=candidate_id, workspace_id=workspace_id, episode_ids=episode_ids)
    except ReplayEvaluationError as error:
        if error.code not in {"conflict"}:
            raise
    with closing(database._connect()) as connection:
        rows = connection.execute(
            "SELECT e.episode_id,e.trusted_skill_snapshot,c.replay_capsule_id FROM episodes e "
            "JOIN run_contexts r ON r.run_id=e.run_id JOIN replay_capsules c ON c.run_id=e.run_id "
            "JOIN evolution_candidate_episode_refs ref ON ref.episode_id=e.episode_id "
            "WHERE ref.candidate_id=? AND r.workspace_id=? AND e.trace_state='complete' AND e.coverage_state='observed' AND e.snapshot_partial=0 AND c.status='ready'",
            (candidate_id, workspace_id),
        ).fetchall()
    if len(rows) != len(episode_ids) or {row[0] for row in rows} != set(episode_ids):
        raise ReplayEvaluationError("replay_authorization_missing")
    row_by_episode = {row[0]: row for row in rows}
    rows = [row_by_episode[episode_id] for episode_id in episode_ids]
    baseline: list[Arm] = []
    candidate: list[Arm] = []
    staging_root = data_dir / "replay-staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=staging_root) as staging:
        staging_path = Path(staging)
        for episode_id, skill_snapshot, capsule_id in rows:
            capsule = read_replay_capsule(data_dir, capsule_id)
            input_dir = staging_path / (episode_id + "-input")
            skill_dir = staging_path / (episode_id + "-skill")
            baseline_artifacts = staging_path / (episode_id + "-baseline-artifacts")
            candidate_artifacts = staging_path / (episode_id + "-candidate-artifacts")
            input_dir.mkdir(); skill_dir.mkdir(); baseline_artifacts.mkdir(); candidate_artifacts.mkdir()
            fixture = capsule.get("fixture") if isinstance(capsule, dict) else capsule
            if not isinstance(fixture, dict):
                fixture = {}
            request = {
                "schema_version": "skilltree-replay-task/v1",
                "episode_id": episode_id,
                "arm": "baseline",
                "task_type": fixture.get("task_type", "repository_verification"),
                "fixture": fixture,
                "asset_snapshot": {"skill_snapshot": capsule.get("skill_snapshot", "")} if isinstance(capsule, dict) else {},
            }
            task_type = request["task_type"]
            extension = None
            if isinstance(task_type, str) and "." in task_type:
                try:
                    extension = resolve_task_type(database, task_type)
                except ExtensionRegistryError as error:
                    if task_type != "repository_verification":
                        raise ReplayEvaluationError(error.code) from error
            pinned_runtime = dict(runtime_state)
            if extension is not None:
                pinned_runtime.update({"extension_id": extension.extension_id, "extension_version": extension.extension_version, "image_name": extension.image_name, "image_digest": extension.image_digest})
            (input_dir / "request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            # Make the selected arm and an optional Python source fixture
            # explicit inputs. The extension must not infer which arm it runs.
            if isinstance(fixture, dict):
                arm_name = "baseline"
                source = fixture.get("baseline_source", fixture.get("source"))
                source_name = fixture.get("source_name", "source.py")
                if (
                    isinstance(source, str)
                    and isinstance(source_name, str)
                    and source_name.endswith(".py")
                    and "/" not in source_name
                    and "\\" not in source_name
                ):
                    (input_dir / source_name).write_text(source, encoding="utf-8")
            (skill_dir / "SKILL.md").write_text(str(skill_snapshot), encoding="utf-8")
            common = {"runtime_state": pinned_runtime, "extension": extension, "episode_id": episode_id, "capsule_id": capsule_id, "input_dir": input_dir, "skill_dir": skill_dir, "docker_path": docker_path}
            (input_dir / "arm.txt").write_text("baseline", encoding="utf-8")
            baseline.append(arm_runner(arm="baseline", artifact_dir=baseline_artifacts, **common))
            request["arm"] = "candidate"
            (input_dir / "request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            if isinstance(fixture, dict):
                candidate_source = fixture.get("candidate_source", fixture.get("source"))
                if (
                    isinstance(candidate_source, str)
                    and isinstance(source_name, str)
                    and source_name.endswith(".py")
                    and "/" not in source_name
                    and "\\" not in source_name
                ):
                    (input_dir / source_name).write_text(candidate_source, encoding="utf-8")
            (input_dir / "arm.txt").write_text("candidate", encoding="utf-8")
            candidate.append(arm_runner(arm="candidate", artifact_dir=candidate_artifacts, **common))
    report = compare_arms(report_id=str(uuid4()), candidate_id=candidate_id, episode_ids=episode_ids, baseline=baseline, candidate=candidate, created_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"))
    if report.verdict == "insufficient" or report.guardrail_breaches:
        return {"candidate_id": candidate_id, "status": "draft", "verdict": report.verdict, "reason": "guardrail_breached"}
    return persist_replay_report(database, report)


@dataclass(frozen=True)
class ReplayReport:
    report_id: str
    candidate_id: str
    dataset_snapshot: dict[str, object]
    baseline_metrics: dict[str, object]
    candidate_metrics: dict[str, object]
    sample_size: int
    effect_size: float
    coverage: dict[str, object]
    guardrail_breaches: tuple[str, ...]
    verdict: str
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {"schema_version": "skilltree/v1", **self.__dict__, "guardrail_breaches": list(self.guardrail_breaches)}


def dataset_snapshot(episode_ids: list[str]) -> dict[str, object]:
    if not 1 <= len(episode_ids) <= 200 or len(set(episode_ids)) != len(episode_ids) or any(not isinstance(item, str) or not item for item in episode_ids):
        raise ReplayEvaluationError("invalid_schema")
    ordered = list(episode_ids)
    content = json.dumps({"episode_ids": ordered}, separators=(",", ":"), sort_keys=True).encode()
    return {"episode_ids": ordered, "content_hash": "sha256:" + hashlib.sha256(content).hexdigest()}


def compare_arms(*, report_id: str, candidate_id: str, episode_ids: list[str], baseline: list[Arm], candidate: list[Arm], created_at: str) -> ReplayReport:
    snapshot = dataset_snapshot(episode_ids)
    if len(baseline) != len(candidate) or len(baseline) != len(episode_ids):
        raise ReplayEvaluationError("pairing_mismatch")
    breaches: list[str] = []
    for arms in (baseline, candidate):
        for expected, arm in zip(episode_ids, arms, strict=True):
            if arm.get("episode_id") != expected:
                breaches.append("dataset_mismatch")
    for arm in baseline + candidate:
        values = arm.get("guardrail_breaches", [])
        if isinstance(values, list):
            breaches.extend(item for item in values if isinstance(item, str))
        if arm.get("verdict") == "unknown":
            breaches.append("result_unknown")
    baseline_metrics = _metrics(baseline)
    candidate_metrics = _metrics(candidate)
    effect = float(candidate_metrics["mean_quality_score"] - baseline_metrics["mean_quality_score"])
    coverage = _coverage(baseline, candidate)
    complete = all(coverage[key] >= 1 for key in ("success", "failure_recovery", "negative"))
    coverage["complete"] = complete
    if breaches:
        verdict = "insufficient"
    elif effect > 0 and candidate_metrics["success_rate"] >= baseline_metrics["success_rate"] and complete:
        verdict = "improved"
    elif effect < 0 or candidate_metrics["success_rate"] < baseline_metrics["success_rate"]:
        verdict = "regressed"
    else:
        verdict = "neutral"
    return ReplayReport(report_id, candidate_id, snapshot, baseline_metrics, candidate_metrics, len(episode_ids), effect, coverage, tuple(sorted(set(breaches))), verdict, created_at)


def transition_candidate(current: CandidateStatus, target: CandidateStatus) -> CandidateStatus:
    allowed = {"draft": {"replay_passed", "rejected", "rolled_back"}, "replay_passed": {"rejected", "rolled_back"}, "rejected": set(), "rolled_back": set()}
    if target not in allowed[current]:
        raise ReplayEvaluationError("invalid_transition")
    return target


def _metrics(arms: list[Arm]) -> dict[str, object]:
    if not arms:
        raise ReplayEvaluationError("invalid_schema")
    scores = [float(item["quality_score"]) for item in arms]
    successes = sum(item.get("verdict") == "success" for item in arms)
    latencies = sorted(int(item["latency_ms"]) for item in arms)
    index = min(len(latencies) - 1, max(0, math.ceil(len(latencies) * 0.95) - 1))
    return {"success_rate": successes / len(arms), "mean_quality_score": sum(scores) / len(scores), "p95_latency_ms": latencies[index]}


def _coverage(baseline: list[Arm], candidate: list[Arm]) -> dict[str, int]:
    success = sum(item.get("verdict") == "success" for item in candidate)
    recovery = sum(base.get("verdict") == "failed" and cand.get("verdict") == "success" for base, cand in zip(baseline, candidate, strict=True))
    negative = sum(item.get("verdict") == "failed" for item in baseline)
    return {"success": success, "failure_recovery": recovery, "negative": negative}


def _uuid(value: str) -> None:
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise ReplayEvaluationError("invalid_schema") from None
