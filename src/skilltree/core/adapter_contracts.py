"""Strict input/output contracts shared by Core and Replay Adapters."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AdapterContractError(ValueError):
    pass


@dataclass(frozen=True)
class TaskRequest:
    episode_id: str
    arm: str
    task_type: str
    fixture: dict[str, Any]
    asset_snapshot: dict[str, Any]


@dataclass(frozen=True)
class AdapterResult:
    verdict: str
    quality_score: float
    latency_ms: int
    error_code: str | None
    guardrail_breaches: tuple[str, ...]
    artifact_refs: tuple[str, ...]


_TASK_TYPE = re.compile(r"(?:org\.skilltree|[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+)\.[a-z0-9_][a-z0-9_.-]*\Z")
_MAX_JSON_BYTES = 1024 * 1024


def validate_task_request(value: object) -> TaskRequest:
    if not isinstance(value, dict) or set(value) != {"schema_version", "episode_id", "arm", "task_type", "fixture", "asset_snapshot"}:
        raise AdapterContractError("invalid_schema")
    if value["schema_version"] != "skilltree-replay-task/v1":
        raise AdapterContractError("invalid_schema")
    episode_id, arm, task_type = value["episode_id"], value["arm"], value["task_type"]
    if not all(isinstance(item, str) and item for item in (episode_id, arm, task_type)) or arm not in {"baseline", "candidate"}:
        raise AdapterContractError("invalid_schema")
    if not _TASK_TYPE.fullmatch(task_type):
        raise AdapterContractError("invalid_task_type")
    fixture, snapshot = value["fixture"], value["asset_snapshot"]
    if not isinstance(fixture, dict) or not isinstance(snapshot, dict) or _json_size(value) > _MAX_JSON_BYTES:
        raise AdapterContractError("input_too_large")
    if _contains_forbidden_key(fixture) or _contains_forbidden_key(snapshot):
        raise AdapterContractError("invalid_schema")
    return TaskRequest(episode_id, arm, task_type, fixture, snapshot)


def validate_adapter_result(value: object, *, episode_id: str, arm: str) -> AdapterResult:
    if not isinstance(value, dict):
        raise AdapterContractError("result_invalid")
    allowed = {"schema_version", "episode_id", "arm", "verdict", "quality_score", "latency_ms", "error_code", "guardrail_breaches", "artifact_refs"}
    required = {"schema_version", "episode_id", "arm", "verdict", "quality_score", "latency_ms"}
    if set(value) - allowed or not required <= set(value):
        raise AdapterContractError("result_invalid")
    if value["schema_version"] != "skilltree/v1" or value["episode_id"] != episode_id or value["arm"] != arm:
        raise AdapterContractError("result_invalid")
    if value["verdict"] not in {"success", "failed", "unknown"} or not isinstance(value["quality_score"], (int, float)) or isinstance(value["quality_score"], bool) or not 0 <= value["quality_score"] <= 1:
        raise AdapterContractError("result_invalid")
    if not isinstance(value["latency_ms"], int) or value["latency_ms"] < 0:
        raise AdapterContractError("result_invalid")
    breaches = value.get("guardrail_breaches", [])
    refs = value.get("artifact_refs", [])
    if not isinstance(breaches, list) or not all(isinstance(item, str) for item in breaches) or not isinstance(refs, list) or not all(isinstance(item, str) for item in refs):
        raise AdapterContractError("result_invalid")
    return AdapterResult(value["verdict"], float(value["quality_score"]), value["latency_ms"], value.get("error_code"), tuple(breaches), tuple(refs))


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        if any(key in {"command", "shell", "cmd", "docker_args", "mounts", "environment"} for key in value):
            return True
        return any(_contains_forbidden_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False
