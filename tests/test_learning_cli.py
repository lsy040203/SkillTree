from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "skilltree", *args], cwd=ROOT, text=True, capture_output=True, check=False)


def _write(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_learning_cli_feedback_outcome_weights_and_rebuild(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    initialized = _run("storage", "initialize", "--data-dir", str(data_dir), "--plugin-root", str(ROOT / "plugins" / "skilltree"), "--target-schema-version", "7", "--json")
    assert initialized.returncode == 0, initialized.stderr
    workspace = "sha256:" + "a" * 64
    feedback = _write(tmp_path / "feedback.json", {
        "schema_version": "skilltree-learning-feedback/v1",
        "workspace_id": workspace,
        "action": "select",
        "skill_names": ["analyze"],
        "evidence_handle": "feedback-1",
        "occurred_at": "2026-01-01T00:00:00Z",
    })
    result = _run("learning", "feedback", "--data-dir", str(data_dir), "--input", str(feedback))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["schema_version"] == "skilltree/v1"

    outcome = _write(tmp_path / "outcome.json", {
        "schema_version": "skilltree-learning-outcome/v1",
        "workspace_id": workspace,
        "assessment_handle": "assessment-1",
        "verdict": "success",
        "coverage_state": "observed",
        "executed_skills": ["analyze", "code-review"],
    })
    result = _run("learning", "outcome", "--data-dir", str(data_dir), "--input", str(outcome))
    assert result.returncode == 0, result.stderr
    assert "analyze" in result.stdout and "code-review" in result.stdout

    result = _run("learning", "weights", "--data-dir", str(data_dir), "--workspace-id", workspace)
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["weights"][0]["skill_name"] == "analyze"

    result = _run("learning", "rebuild", "--data-dir", str(data_dir), "--workspace-id", workspace, "--as-of", "2026-03-01T00:00:00Z")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["schema_version"] == "skilltree/v1"


def test_learning_cli_rejects_unknown_fields(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _run("storage", "initialize", "--data-dir", str(data_dir), "--plugin-root", str(ROOT / "plugins" / "skilltree"), "--target-schema-version", "7", "--json")
    workspace = "sha256:" + "a" * 64
    request = _write(tmp_path / "bad.json", {
        "schema_version": "skilltree-learning-feedback/v1",
        "workspace_id": workspace,
        "action": "select",
        "skill_names": ["analyze"],
        "evidence_handle": "feedback-1",
        "prompt": "must not be accepted",
    })
    result = _run("learning", "feedback", "--data-dir", str(data_dir), "--input", str(request))
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "invalid_schema"
