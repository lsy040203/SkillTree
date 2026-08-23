from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


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
    guardrail_breaches: list[str]
    error_code: str | None = None


class TaskAdapter(Protocol):
    task_type: str

    def run(self, request: TaskRequest) -> AdapterResult: ...
