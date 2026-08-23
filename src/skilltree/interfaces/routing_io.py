"""Strict JSON-file request parsing for the P2 routing CLI boundary."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


MAX_INPUT_BYTES = 32 * 1024
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ROUTE_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class RouteInputError(ValueError):
    """Raised for every public route request-schema violation."""

    def __init__(self) -> None:
        super().__init__("invalid_schema")


def load_route_request(input_path: Path, command: str) -> dict[str, object]:
    """Load a bounded JSON request without retaining prompt or token content."""
    if command not in {"prepare", "commit"} or not _is_absolute_local_path(input_path):
        raise RouteInputError()
    try:
        contents = input_path.read_bytes()
        if len(contents) > MAX_INPUT_BYTES:
            raise RouteInputError()
        parsed = json.loads(contents.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise RouteInputError() from None
    if not isinstance(parsed, dict):
        raise RouteInputError()
    if command == "prepare":
        _validate_prepare_request(parsed)
    else:
        _validate_commit_request(parsed)
    return parsed


def load_route_candidates_stdin(contents: bytes) -> dict[str, object]:
    """Parse the bounded candidate-only fallback request from stdin."""
    if not isinstance(contents, bytes) or len(contents) > MAX_INPUT_BYTES:
        raise RouteInputError()
    try:
        parsed = json.loads(contents.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RouteInputError() from None
    if not isinstance(parsed, dict) or set(parsed) != {"schema_version", "prompt"}:
        raise RouteInputError()
    prompt = parsed["prompt"]
    if parsed["schema_version"] != "skilltree-route-candidates/v1" or not isinstance(prompt, str) or not prompt or len(prompt.encode("utf-8")) > 16 * 1024:
        raise RouteInputError()
    return parsed


def _validate_prepare_request(value: dict[str, object]) -> None:
    if set(value) != {"schema_version", "workspace_id", "session_id_hash", "prompt"}:
        raise RouteInputError()
    if (
        value["schema_version"] != "skilltree/v1"
        or not _is_sha256(value["workspace_id"])
        or not _is_sha256(value["session_id_hash"])
        or not isinstance(value["prompt"], str)
        or not value["prompt"]
    ):
        raise RouteInputError()


def _validate_commit_request(value: dict[str, object]) -> None:
    if set(value) != {"schema_version", "route_token", "workspace_id", "session_id_hash", "decision"}:
        raise RouteInputError()
    if (
        value["schema_version"] != "skilltree-route-commit/v1"
        or not isinstance(value["route_token"], str)
        or _ROUTE_TOKEN.fullmatch(value["route_token"]) is None
        or not _is_sha256(value["workspace_id"])
        or not _is_sha256(value["session_id_hash"])
        or not isinstance(value["decision"], dict)
    ):
        raise RouteInputError()


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RouteInputError()
        result[key] = value
    return result


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _is_absolute_local_path(path: Path) -> bool:
    raw = str(path)
    return path.is_absolute() and not raw.startswith("\\\\") and not any(character in raw for character in "*?")
