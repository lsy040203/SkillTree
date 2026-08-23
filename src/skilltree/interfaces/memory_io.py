"""Bounded parsers for P5 memory CLI request files."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID


class MemoryRequestError(ValueError):
    def __init__(self, code: str = "invalid_schema") -> None:
        self.code = code
        super().__init__(code)


def read_memory_request(path: Path) -> dict[str, object]:
    """Read one UTF-8 JSON request file without returning its source path."""
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > 16 * 1024:
        raise MemoryRequestError()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MemoryRequestError() from error
    if not isinstance(value, dict):
        raise MemoryRequestError()
    return value


def load_memory_request(path: Path, command: str) -> dict[str, object]:
    """Validate the compact owner-scoped P5 lifecycle request."""
    value = read_memory_request(path)
    required = {"schema_version", "user_id"}
    optional: set[str] = set()
    if command in {"approve", "reject"}:
        required |= {"workspace_id", "candidate_id"}
    elif command == "profile-extract":
        required |= {"workspace_id", "durable_preference_statements"}
        optional = {"transient_user_instructions", "response_feedback"}
    elif command == "candidate-list":
        required.add("workspace_id")
    elif command == "list":
        required.add("layer")
        optional = {"workspace_id", "include_hidden"}
    elif command == "delete":
        required |= {"layer", "handle"}
        optional = {"workspace_id"}
    elif command == "export":
        required.add("workspace_id")
    elif command == "clear-profile":
        required.add("confirm")
    elif command == "clear-workspace-data":
        required |= {"workspace_id", "confirm"}
    else:
        required.add("workspace_id")
    if not set(value) <= required | optional or not required <= set(value) or value.get("schema_version") != "skilltree/v1":
        raise MemoryRequestError()
    scalar_required = required - {"durable_preference_statements"}
    if not all(isinstance(value[field], str) and value[field] for field in scalar_required):
        raise MemoryRequestError()
    if value.get("user_id") != "local":
        raise MemoryRequestError()
    if command == "list":
        if value["layer"] not in {"L1", "L2"}:
            raise MemoryRequestError()
        if value["layer"] == "L1" and ({"workspace_id", "include_hidden"} & set(value)):
            raise MemoryRequestError()
        if value["layer"] == "L2" and "workspace_id" not in value:
            raise MemoryRequestError()
        if "include_hidden" in value and not isinstance(value["include_hidden"], bool):
            raise MemoryRequestError()
    if command == "delete":
        if value["layer"] not in {"L1", "L2"}:
            raise MemoryRequestError()
        if value["layer"] == "L1" and "workspace_id" in value:
            raise MemoryRequestError()
        if value["layer"] == "L2" and "workspace_id" not in value:
            raise MemoryRequestError()
        handle = value["handle"]
        if value["layer"] == "L1":
            parts = handle.split(".")
            if len(parts) != 2 or not all(parts) or any(not part.replace("_", "").isalnum() for part in parts):
                raise MemoryRequestError()
        else:
            try:
                if str(UUID(handle)) != handle:
                    raise ValueError
            except (ValueError, AttributeError):
                raise MemoryRequestError() from None
    if command == "export" and not isinstance(value["workspace_id"], str):
        raise MemoryRequestError()
    if command == "clear-profile" and value["confirm"] != "DELETE_PROFILE":
        raise MemoryRequestError("authorization_required")
    if command == "clear-workspace-data" and value["confirm"] != "DELETE_WORKSPACE_DATA":
        raise MemoryRequestError("authorization_required")
    if command == "profile-extract":
        statements = value["durable_preference_statements"]
        transient = value.get("transient_user_instructions", [])
        feedback = value.get("response_feedback", "none")
        if (
            not isinstance(statements, list) or not 1 <= len(statements) <= 8
            or any(not isinstance(item, str) or not item or len(item) > 1000 for item in statements)
            or not isinstance(transient, list) or len(transient) > 8
            or any(not isinstance(item, str) or not item or len(item) > 1000 for item in transient)
            or not isinstance(feedback, str) or len(feedback) > 300
        ):
            raise MemoryRequestError()
        value["transient_user_instructions"] = transient
        value["response_feedback"] = feedback
    return value
