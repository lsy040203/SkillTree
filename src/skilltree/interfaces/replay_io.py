"""Strict request parsing for P6 replay-extension lifecycle commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MAX_INPUT_BYTES = 16 * 1024


class ReplayInputError(ValueError):
    def __init__(self, code: str = "invalid_schema") -> None:
        self.code = code
        super().__init__(code)


def load_replay_request(path: Path, command: str) -> dict[str, str]:
    if command not in {"install-extension", "uninstall-extension", "enable-extension", "disable-extension", "remove-extension"}:
        raise ReplayInputError()
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ReplayInputError()
    try:
        value = json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_unique)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ReplayInputError):
        raise ReplayInputError() from None
    if not isinstance(value, dict) or any(item is None for item in value.values()):
        raise ReplayInputError()
    expected = {"schema_version", "user_id", "confirm"}
    if command == "install-extension":
        expected.add("extension_root")
    elif command != "uninstall-extension":
        expected.add("extension_id")
    if set(value) != expected or not all(isinstance(item, str) and item for item in value.values()):
        raise ReplayInputError()
    if value["schema_version"] != "skilltree/v1" or value["user_id"] != "local":
        raise ReplayInputError()
    expected_confirmation = {"install-extension": "INSTALL_REPLAY_EXTENSION", "uninstall-extension": "UNINSTALL_REPLAY_EXTENSION", "enable-extension": "ENABLE_REPLAY_EXTENSION", "disable-extension": "DISABLE_REPLAY_EXTENSION", "remove-extension": "REMOVE_REPLAY_EXTENSION"}[command]
    if value["confirm"] != expected_confirmation:
        raise ReplayInputError("authorization_required")
    if command == "install-extension" and not _absolute_local(Path(value["extension_root"])):
        raise ReplayInputError("out_of_scope")
    return value


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReplayInputError()
        result[key] = value
    return result


def _absolute_local(path: Path) -> bool:
    raw = str(path)
    return path.is_absolute() and not raw.startswith("\\\\") and not any(c in raw for c in "*?")
