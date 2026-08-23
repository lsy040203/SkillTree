from __future__ import annotations

import json
from pathlib import Path

from skilltree.application.cli import build_parser
from skilltree.interfaces.replay_io import load_replay_request


def test_registry_commands_are_exposed() -> None:
    parser = build_parser()
    args = parser.parse_args(["replay", "list-extensions"])
    assert args.replay_command == "list-extensions"
    args = parser.parse_args(["replay", "disable-extension", "--input", "C:/request.json"])
    assert args.replay_command == "disable-extension"


def test_extension_state_request_requires_exact_confirmation(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps({"schema_version": "skilltree/v1", "user_id": "local", "confirm": "DISABLE_REPLAY_EXTENSION", "extension_id": "com.example.one"}), encoding="utf-8")
    assert load_replay_request(path, "disable-extension")["extension_id"] == "com.example.one"
    path.write_text(json.dumps({"schema_version": "skilltree/v1", "user_id": "local", "confirm": "DISABLE_REPLAY_EXTENSION", "extension_id": "com.example.one", "extra": 1}), encoding="utf-8")
    try:
        load_replay_request(path, "disable-extension")
    except ValueError as error:
        assert getattr(error, "code", "") == "invalid_schema"
    else:
        raise AssertionError("unknown fields must be rejected")
