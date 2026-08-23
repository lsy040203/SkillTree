"""Strict P6 evolve scan request parser."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class EvolveInputError(ValueError):
    def __init__(self, code: str = "invalid_schema") -> None:
        self.code = code
        super().__init__(code)


def load_evolve_request(path: Path) -> dict[str, object]:
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > 16 * 1024:
        raise EvolveInputError()
    try:
        value = json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_unique)
    except (OSError, UnicodeDecodeError, ValueError, EvolveInputError):
        raise EvolveInputError() from None
    if not isinstance(value, dict) or set(value) != {"schema_version", "user_id", "workspace_id", "candidate_id", "episode_ids", "confirm"}:
        raise EvolveInputError()
    if value["schema_version"] != "skilltree/v1" or value["user_id"] != "local" or value["confirm"] != "RUN_EVOLVE_SCAN":
        raise EvolveInputError("authorization_required" if value.get("confirm") != "RUN_EVOLVE_SCAN" else "invalid_schema")
    if not isinstance(value["workspace_id"], str) or not value["workspace_id"] or not isinstance(value["candidate_id"], str) or not value["candidate_id"]:
        raise EvolveInputError()
    episodes = value["episode_ids"]
    if not isinstance(episodes, list) or not 1 <= len(episodes) <= 200 or any(not isinstance(item, str) or not item for item in episodes):
        raise EvolveInputError()
    return value


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvolveInputError()
        result[key] = value
    return result
