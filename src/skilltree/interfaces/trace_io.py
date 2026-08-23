"""Bounded JSON-file parsing for explicit P3.2 outcome assessments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MAX_INPUT_BYTES = 16 * 1024
SCHEMA_VERSION = "skilltree-trace-outcome/v1"
_SOURCES = {"user", "read_only_verifier", "tool_adapter"}
_VERDICTS = {"success", "failed", "cancelled", "unknown"}


class TraceInputError(ValueError):
    """Raised for a public trace request-schema violation."""

    def __init__(self) -> None:
        super().__init__("invalid_schema")


def load_trace_outcome_request(input_path: Path) -> dict[str, str]:
    """Load one explicit, bounded outcome assessment request."""
    if not _is_absolute_local_path(input_path):
        raise TraceInputError()
    try:
        contents = input_path.read_bytes()
        if len(contents) > MAX_INPUT_BYTES:
            raise TraceInputError()
        parsed = json.loads(contents.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise TraceInputError() from None
    expected = {
        "schema_version", "run_id", "turn_trace_id", "event_id", "source",
        "verdict", "outcome_summary", "evidence_ref",
    }
    if not isinstance(parsed, dict) or set(parsed) != expected or not all(isinstance(value, str) for value in parsed.values()):
        raise TraceInputError()
    if (
        parsed["schema_version"] != SCHEMA_VERSION
        or not all(parsed[field] for field in ("run_id", "turn_trace_id", "event_id", "outcome_summary"))
        or parsed["source"] not in _SOURCES
        or parsed["verdict"] not in _VERDICTS
        or len(parsed["outcome_summary"].encode("utf-8")) > 2048
    ):
        raise TraceInputError()
    return parsed


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TraceInputError()
        result[key] = value
    return result


def _is_absolute_local_path(path: Path) -> bool:
    raw = str(path)
    return path.is_absolute() and not raw.startswith("\\\\") and not any(character in raw for character in "*?")
