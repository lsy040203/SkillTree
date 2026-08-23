from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

from skilltree.core.replay_runner import run_arm


def test_run_arm_uses_fixed_fixture_only_docker_arguments(tmp_path: Path) -> None:
    input_dir, skill_dir, artifact_dir = (tmp_path / name for name in ("input", "skill", "artifacts"))
    input_dir.mkdir(); skill_dir.mkdir(); artifact_dir.mkdir()
    docker = tmp_path / "docker.exe"; docker.write_bytes(b"x")
    calls = []

    def fake(args, **kwargs):
        calls.append(args)
        if args[1] == "run":
            (artifact_dir / "result.json").write_text(json.dumps({"schema_version": "skilltree/v1", "episode_id": "e", "arm": "baseline", "verdict": "success", "quality_score": 0.8, "latency_ms": 12, "artifact_refs": []}))
        return CompletedProcess(args, 0, "", "")

    result = run_arm(runtime_state={"schema_version": "skilltree-replay-runtime/v1", "image_name": "runner", "image_digest": "sha256:" + "a" * 64}, episode_id="e", capsule_id="c", arm="baseline", input_dir=input_dir, skill_dir=skill_dir, artifact_dir=artifact_dir, docker_path=docker, runner=fake)
    assert result["verdict"] == "success"
    assert "--network" in calls[0] and calls[0][calls[0].index("--network") + 1] == "none"
    assert "--privileged" not in calls[0]


def test_run_arm_maps_timeout_to_unknown(tmp_path: Path) -> None:
    input_dir, skill_dir, artifact_dir = (tmp_path / name for name in ("input", "skill", "artifacts"))
    input_dir.mkdir(); skill_dir.mkdir(); artifact_dir.mkdir()
    docker = tmp_path / "docker.exe"; docker.write_bytes(b"x")
    def fake(*args, **kwargs):
        if args[0][1] == "rm":
            return CompletedProcess(args[0], 0, "", "")
        raise __import__("subprocess").TimeoutExpired(args[0], 1)
    result = run_arm(runtime_state={"schema_version": "skilltree-replay-runtime/v1", "image_name": "runner", "image_digest": "sha256:" + "a" * 64}, episode_id="e", capsule_id="c", arm="candidate", input_dir=input_dir, skill_dir=skill_dir, artifact_dir=artifact_dir, docker_path=docker, runner=fake)
    assert result["verdict"] == "unknown" and result["error_code"] == "timeout"
