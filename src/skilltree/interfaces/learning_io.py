"""Bounded JSON-file parsing for P4 learning commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MAX_INPUT_BYTES = 16 * 1024


class LearningInputError(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid_schema")


def load_learning_request(input_path: Path, command: str) -> dict[str, object]:
    if command not in {"feedback", "outcome"} or not _is_absolute_local_path(input_path):
        raise LearningInputError()
    try:
        contents = input_path.read_bytes()
        if len(contents) > MAX_INPUT_BYTES:
            raise LearningInputError()
        parsed = json.loads(contents.decode("utf-8"), object_pairs_hook=_without_duplicates)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise LearningInputError() from None
    if not isinstance(parsed, dict):
        raise LearningInputError()
    if command == "feedback":
        expected = {"schema_version", "workspace_id", "action", "skill_names", "evidence_handle"}
        optional = {"occurred_at"}
        if not expected <= set(parsed) or set(parsed) - expected - optional or parsed["schema_version"] != "skilltree-learning-feedback/v1":
            raise LearningInputError()
        if not isinstance(parsed["workspace_id"], str) or not isinstance(parsed["action"], str) or not isinstance(parsed["evidence_handle"], str):
            raise LearningInputError()
        if not isinstance(parsed["skill_names"], list) or not all(isinstance(item, str) for item in parsed["skill_names"]):
            raise LearningInputError()
        return parsed
    expected = {"schema_version", "workspace_id", "assessment_handle", "verdict", "coverage_state"}
    optional = {"executed_skills", "failed_skills", "selected_skill", "occurred_at"}
    if not expected <= set(parsed) or set(parsed) - expected - optional or parsed["schema_version"] != "skilltree-learning-outcome/v1":
        raise LearningInputError()
    if not all(isinstance(parsed[key], str) for key in expected):
        raise LearningInputError()
    for key in ("executed_skills", "failed_skills"):
        if key in parsed and (not isinstance(parsed[key], list) or not all(isinstance(item, str) for item in parsed[key])):
            raise LearningInputError()
    if "selected_skill" in parsed and not isinstance(parsed["selected_skill"], str):
        raise LearningInputError()
    return parsed


def _without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LearningInputError()
        result[key] = value
    return result


def _is_absolute_local_path(path: Path) -> bool:
    raw = str(path)
    return path.is_absolute() and not raw.startswith("\\\\") and not any(character in raw for character in "*?")
