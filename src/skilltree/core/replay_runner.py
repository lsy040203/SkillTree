"""Strict Docker-only fixture replay runner for P6."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Callable


class ReplayRunnerError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def run_arm(
    *,
    runtime_state: dict[str, str],
    episode_id: str,
    capsule_id: str,
    arm: str,
    input_dir: Path,
    skill_dir: Path,
    artifact_dir: Path,
    timeout_ms: int = 60000,
    docker_path: Path,
    extension: object | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    if arm not in {"baseline", "candidate"} or not 1000 <= timeout_ms <= 300000:
        raise ReplayRunnerError("invalid_schema")
    if not isinstance(runtime_state, dict) or runtime_state.get("schema_version") != "skilltree-replay-runtime/v1":
        raise ReplayRunnerError("replay_runtime_unavailable")
    image = runtime_state.get("image_name")
    digest = runtime_state.get("image_digest")
    if not isinstance(image, str) or not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ReplayRunnerError("replay_runtime_unavailable")
    if extension is not None:
        expected = (getattr(extension, "image_name", ""), getattr(extension, "image_digest", ""))
        if expected != (image, digest) or not getattr(extension, "enabled", False):
            raise ReplayRunnerError("task_type_unavailable")
    roots = [input_dir, skill_dir, artifact_dir]
    if any(not path.is_absolute() or not path.is_dir() or path.is_symlink() for path in roots):
        raise ReplayRunnerError("out_of_scope")
    artifact_dir_empty = not any(artifact_dir.iterdir())
    if not artifact_dir_empty:
        raise ReplayRunnerError("out_of_scope")
    container = "skilltree-replay-" + uuid.uuid4().hex
    command = [
        str(docker_path), "run", "--rm", "--name", container,
        "--user", "65532:65532", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--read-only", "--network", "none",
        "--cpus", "1", "--memory", "512m", "--pids-limit", "64",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "-v", f"{input_dir.resolve()}:/input:ro",
        "-v", f"{skill_dir.resolve()}:/skill:ro",
        "-v", f"{artifact_dir.resolve()}:/artifacts:rw",
        f"{image}@{digest}",
    ]
    call = runner or subprocess.run
    try:
        result = call(command, capture_output=True, text=True, check=False, timeout=timeout_ms / 1000)
    except subprocess.TimeoutExpired:
        cleaned = _cleanup(docker_path, container, call)
        return _unknown(episode_id, arm, "container_cleanup_failed" if not cleaned else "timeout")
    except (OSError, subprocess.SubprocessError):
        return _unknown(episode_id, arm, "container_create_mismatch")
    if result.returncode != 0:
        cleaned = _cleanup(docker_path, container, call)
        return _unknown(episode_id, arm, "container_cleanup_failed" if not cleaned else "result_invalid")
    result_path = artifact_dir / "result.json"
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        cleaned = _cleanup(docker_path, container, call)
        return _unknown(episode_id, arm, "container_cleanup_failed" if not cleaned else "result_invalid")
    if not _cleanup(docker_path, container, call):
        return _unknown(episode_id, arm, "container_cleanup_failed")
    if not isinstance(payload, dict) or not _valid_result(payload, episode_id, arm):
        return _unknown(episode_id, arm, "result_invalid")
    payload.pop("artifact_refs", None)
    payload["artifact_refs"] = []
    payload["capsule_id"] = capsule_id
    return payload


def _valid_result(value: dict[str, object], episode_id: str, arm: str) -> bool:
    required = {"schema_version", "episode_id", "arm", "verdict", "quality_score", "latency_ms"}
    if set(value) - required - {"error_code", "guardrail_breaches", "artifact_refs"} or not required <= set(value):
        return False
    return (
        value["schema_version"] == "skilltree/v1"
        and value["episode_id"] == episode_id
        and value["arm"] == arm
        and value["verdict"] in {"success", "failed", "cancelled", "unknown"}
        and isinstance(value["quality_score"], (int, float)) and not isinstance(value["quality_score"], bool) and 0 <= value["quality_score"] <= 1
        and isinstance(value["latency_ms"], int) and value["latency_ms"] >= 0
        and isinstance(value.get("guardrail_breaches", []), list)
    )


def _unknown(episode_id: str, arm: str, reason: str) -> dict[str, object]:
    return {"schema_version": "skilltree/v1", "episode_id": episode_id, "arm": arm, "verdict": "unknown", "quality_score": 0.0, "latency_ms": 0, "error_code": reason, "guardrail_breaches": [reason], "artifact_refs": []}


def _cleanup(docker_path: Path, container: str, call: Callable[..., subprocess.CompletedProcess[str]]) -> bool:
    try:
        result = call([str(docker_path), "rm", "-f", container], capture_output=True, text=True, check=False, timeout=5)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
