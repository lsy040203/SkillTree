"""Strict JSON-file parsing for the RuntimeConfig consent CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal


MAX_INPUT_BYTES = 16 * 1024
SCHEMA_VERSION = "skilltree/v1"
LOCAL_USER_ID = "local"
CONSENT_KEYS = {
    "trace_capture_enabled",
    "memory_read_enabled",
    "memory_write_enabled",
    "replay_capture_enabled",
}


class ConsentInputError(ValueError):
    """Raised for every public consent request-schema violation."""

    def __init__(self) -> None:
        super().__init__("invalid_schema")


def load_consent_request(input_path: Path, command: Literal["status", "set-consent"]) -> dict[str, object]:
    """Load one bounded absolute JSON file and validate its exact command schema."""
    if command not in {"status", "set-consent"} or not _is_absolute_local_path(input_path):
        raise ConsentInputError()
    try:
        contents = input_path.read_bytes()
        if len(contents) > MAX_INPUT_BYTES:
            raise ConsentInputError()
        parsed = json.loads(contents.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ConsentInputError() from None
    if not isinstance(parsed, dict) or _contains_null(parsed):
        raise ConsentInputError()

    if command == "status":
        if set(parsed) != {"schema_version", "user_id"}:
            raise ConsentInputError()
        if parsed["schema_version"] != SCHEMA_VERSION or parsed["user_id"] != LOCAL_USER_ID:
            raise ConsentInputError()
        if not all(isinstance(parsed[key], str) for key in ("schema_version", "user_id")):
            raise ConsentInputError()
        return parsed

    if set(parsed) != {"schema_version", "user_id", "expected_config_version", "consents", "confirm"}:
        raise ConsentInputError()
    if parsed["schema_version"] != SCHEMA_VERSION or parsed["user_id"] != LOCAL_USER_ID:
        raise ConsentInputError()
    if not isinstance(parsed["expected_config_version"], int) or isinstance(parsed["expected_config_version"], bool):
        raise ConsentInputError()
    if parsed["expected_config_version"] < 1 or parsed["confirm"] != "SET_RUNTIME_CONSENT":
        raise ConsentInputError()
    consents = parsed["consents"]
    if not isinstance(consents, dict) or set(consents) != CONSENT_KEYS:
        raise ConsentInputError()
    if not all(isinstance(value, bool) for value in consents.values()):
        raise ConsentInputError()
    return parsed


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConsentInputError()
        result[key] = value
    return result


def _contains_null(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, dict):
        return any(_contains_null(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_null(item) for item in value)
    return False


def _is_absolute_local_path(path: Path) -> bool:
    raw = str(path)
    return path.is_absolute() and not raw.startswith("\\\\") and not any(character in raw for character in "*?")
