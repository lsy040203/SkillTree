from __future__ import annotations

import json
from pathlib import Path

from adapters.base import TaskRequest
from adapters.registry import build_registry


def main() -> int:
    input_root = Path("/input")
    artifact_root = Path("/artifacts")
    try:
        payload = json.loads((input_root / "request.json").read_text(encoding="utf-8"))
        request = _parse(payload)
        adapter = build_registry(input_root).get(request.task_type)
        if adapter is None:
            result = {"verdict": "failed", "quality_score": 0.0, "latency_ms": 0, "error_code": "unsupported_task_type", "guardrail_breaches": []}
        else:
            outcome = adapter.run(request)
            result = {"verdict": outcome.verdict, "quality_score": outcome.quality_score, "latency_ms": outcome.latency_ms, "error_code": outcome.error_code, "guardrail_breaches": outcome.guardrail_breaches}
        result.update({"schema_version": "skilltree/v1", "episode_id": request.episode_id, "arm": request.arm, "artifact_refs": []})
    except Exception as error:
        result = {"schema_version": "skilltree/v1", "episode_id": str(payload.get("episode_id", "unknown")) if isinstance(locals().get("payload"), dict) else "unknown", "arm": str(payload.get("arm", "unknown")) if isinstance(locals().get("payload"), dict) else "unknown", "verdict": "unknown", "quality_score": 0.0, "latency_ms": 0, "error_code": "invalid_task", "guardrail_breaches": ["invalid_task"], "artifact_refs": []}
    (artifact_root / "result.json").write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    return 0


def _parse(value: object) -> TaskRequest:
    if not isinstance(value, dict) or value.get("schema_version") != "skilltree-replay-task/v1":
        raise ValueError("invalid_schema")
    episode_id = value.get("episode_id")
    arm = value.get("arm")
    task_type = value.get("task_type")
    fixture = value.get("fixture", {})
    asset_snapshot = value.get("asset_snapshot", {})
    if not all(isinstance(item, str) and item for item in (episode_id, arm, task_type)) or arm not in {"baseline", "candidate"} or not isinstance(fixture, dict) or not isinstance(asset_snapshot, dict):
        raise ValueError("invalid_schema")
    return TaskRequest(episode_id, arm, task_type, fixture, asset_snapshot)


if __name__ == "__main__":
    raise SystemExit(main())
