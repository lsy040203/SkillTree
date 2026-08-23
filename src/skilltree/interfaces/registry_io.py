"""Strict JSON-file request parsing for the P1 registry CLI."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


MAX_INPUT_BYTES = 16 * 1024
SCHEMA_VERSION = "skilltree/v1"
LOCAL_USER_ID = "local"
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class RegistryInputError(ValueError):
    """Raised for every public registry request-schema violation."""

    def __init__(self) -> None:
        super().__init__("invalid_schema")


def load_registry_request(input_path: Path, command: str) -> dict[str, str]:
    """Load one bounded JSON object and validate the exact command schema."""
    if command not in {"setup", "scan", "trust", "block", "status"}:
        raise RegistryInputError()
    if not _is_absolute_local_path(input_path):
        raise RegistryInputError()
    try:
        contents = input_path.read_bytes()
        if len(contents) > MAX_INPUT_BYTES:
            raise RegistryInputError()
        parsed = json.loads(contents.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise RegistryInputError() from None
    if not isinstance(parsed, dict) or any(value is None for value in parsed.values()):
        raise RegistryInputError()

    expected = {"schema_version", "user_id"}
    if command == "setup":
        expected |= {"selected_root", "confirm"}
        if "provided_root" in parsed:
            expected.add("provided_root")
    elif command in {"trust", "block"}:
        expected |= {"name", "content_hash"}
    if set(parsed) != expected or not all(isinstance(value, str) for value in parsed.values()):
        raise RegistryInputError()
    if parsed["schema_version"] != SCHEMA_VERSION or parsed["user_id"] != LOCAL_USER_ID:
        raise RegistryInputError()
    if command == "setup":
        if (
            parsed["confirm"] != "SET_SKILL_ROOT"
            or not _is_absolute_local_path(Path(parsed["selected_root"]))
            or ("provided_root" in parsed and not _is_absolute_local_path(Path(parsed["provided_root"])))
        ):
            raise RegistryInputError()
    if command in {"trust", "block"} and (
        _SKILL_NAME.fullmatch(parsed["name"]) is None or _SHA256.fullmatch(parsed["content_hash"]) is None
    ):
        raise RegistryInputError()
    return parsed


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RegistryInputError()
        result[key] = value
    return result


def _is_absolute_local_path(path: Path) -> bool:
    raw = str(path)
    return path.is_absolute() and not raw.startswith("\\\\") and not any(character in raw for character in "*?")
