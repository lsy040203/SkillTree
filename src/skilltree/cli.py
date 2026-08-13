from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from skilltree.config import RuntimeConfig, SkillRootError
from skilltree.storage import Database


def default_data_dir() -> Path:
    configured = os.environ.get("SKILLTREE_DATA_DIR")
    if configured:
        return Path(configured)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "SkillTree"
    return Path.home() / ".skilltree"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skilltree")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("setup", "status", "doctor"):
        child = subparsers.add_parser(command)
        child.add_argument("--data-dir", type=Path, default=default_data_dir())
        child.add_argument("--json", action="store_true")
        if command == "setup":
            child.add_argument("--skill-root", type=Path, required=True)
            child.add_argument("--yes", action="store_true", help="confirm the supplied local skill root")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    config = RuntimeConfig.load(data_dir)
    database = Database(data_dir / "skilltree.sqlite3")

    if args.command == "setup":
        try:
            config.set_skill_root(args.skill_root, confirmed=args.yes)
        except SkillRootError as error:
            return _emit({"status": "authorization_required", "error": str(error)}, args.json, 2)
        database.migrate()
        return _emit(_status_payload(config, database), args.json)

    if args.command == "status":
        database.migrate()
        return _emit(_status_payload(config, database), args.json)

    runtime_ready = config.path.is_file() and (data_dir / "skilltree.sqlite3").is_file()
    return _emit(
        {
            **_status_payload(config, database),
            "runtime_ready": runtime_ready,
            "diagnostic": "ready" if runtime_ready else "run skilltree setup with an explicitly confirmed skill root",
        },
        args.json,
        0 if runtime_ready else 2,
    )


def _status_payload(config: RuntimeConfig, database: Database) -> dict[str, object]:
    return {
        "skill_root": str(config.skill_root) if config.skill_root else None,
        "trace_capture_enabled": config.trace_capture_enabled,
        "memory_read_enabled": config.memory_read_enabled,
        "memory_write_enabled": config.memory_write_enabled,
        "replay_capture_enabled": config.replay_capture_enabled,
        "schema_versions": database.applied_migrations() if database.path.is_file() else [],
    }


def _emit(payload: dict[str, object], as_json: bool, exit_code: int = 0) -> int:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    return exit_code
