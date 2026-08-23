"""Read-only runtime integrity checks for the installed SkillTree Core."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from skilltree.core.bundle import BundleValidationError, validate_bundle


CHECK_NAMES = (
    "runtime_state",
    "venv_python",
    "bundle_manifest",
    "versions",
    "schema_migrations",
    "hook_bundle",
    "hook_observation",
)


def diagnose(data_dir: Path, *, include_replay: bool = False) -> tuple[dict[str, object], int]:
    """Return the fixed doctor payload without changing files or SQLite state."""
    data_dir = data_dir.expanduser().resolve()
    state, state_check = _runtime_state(data_dir)
    checks: list[dict[str, str]] = [state_check]

    expected_python = data_dir / "venv" / "Scripts" / "python.exe"
    if not expected_python.is_file():
        checks.append(_check("venv_python", "fail", "venv_python_missing"))
    elif Path(sys.executable).resolve() != expected_python.resolve():
        checks.append(_check("venv_python", "fail", "venv_python_mismatch"))
    else:
        checks.append(_check("venv_python", "pass", "ok"))

    manifest: dict[str, Any] | None = None
    plugin_root = _plugin_root(state)
    if plugin_root is None:
        checks.append(_check("bundle_manifest", "fail", "plugin_root_invalid"))
    else:
        try:
            manifest = validate_bundle(plugin_root)
        except BundleValidationError as error:
            checks.append(_check("bundle_manifest", "fail", _bundle_error_code(str(error))))
        else:
            checks.append(_check("bundle_manifest", "pass", "ok"))

    checks.append(_versions_check(state, manifest))
    checks.append(_migrations_check(data_dir, manifest))
    checks.append(_hook_bundle_check(state, manifest))
    hook_check, last_observed_at = _hook_observation_check(data_dir, manifest)
    checks.append(hook_check)

    primary_checks = checks[:6]
    runtime_ready = all(check["state"] == "pass" for check in primary_checks)
    hook_check = checks[-1]
    if not runtime_ready:
        diagnostic_state, exit_code = "failed", 2
    elif hook_check["state"] == "pass":
        diagnostic_state, exit_code = "ready", 0
    else:
        diagnostic_state, exit_code = "degraded", 1
    current_hook_hash = manifest["hook_bundle"]["hash"] if manifest is not None else None
    payload: dict[str, object] = {
        "schema_version": "skilltree-doctor/v1",
        "runtime_ready": runtime_ready,
        "diagnostic_state": diagnostic_state,
        "checks": checks,
        "hook_observation_state": "observed" if hook_check["state"] == "pass" else "unconfirmed",
        "current_hook_bundle_hash": current_hook_hash,
        "last_observed_at": last_observed_at,
    }
    if include_replay:
        from skilltree.core.replay_extension import replay_diagnose

        replay_payload, replay_code = replay_diagnose(data_dir)
        payload.update(replay_payload)
        if exit_code == 0:
            exit_code = replay_code
        payload["extensions"] = _extension_summary(data_dir)
    return payload, exit_code


def _extension_summary(data_dir: Path) -> dict[str, object]:
    try:
        with sqlite3.connect(f"file:{(data_dir / 'skilltree.sqlite3').as_posix()}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT extension_id, extension_version, adapter_name, task_types_json, image_digest, trust_state, install_state FROM replay_extensions ORDER BY extension_id"
            ).fetchall()
    except sqlite3.Error:
        return {"count": 0, "enabled_task_types": [], "records": []}
    records = [{"extension_id": row[0], "extension_version": row[1], "adapter_name": row[2], "task_types": json.loads(row[3]), "image_digest": row[4], "trust_state": row[5], "install_state": row[6]} for row in rows]
    return {"count": len(records), "enabled_task_types": sorted({task for row in records if row[5] in {"official", "local_unverified"} and row[6] == "installed" for task in row[3]}), "records": records}


def _runtime_state(data_dir: Path) -> tuple[dict[str, object] | None, dict[str, str]]:
    path = data_dir / "runtime-state.json"
    if not path.is_file():
        return None, _check("runtime_state", "fail", "runtime_state_missing")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, _check("runtime_state", "fail", "runtime_state_invalid")
    required = {
        "schema_version", "plugin_root", "plugin_version", "core_version",
        "skilltree_schema_version", "bundle_hash", "hook_bundle_hash", "installed_at",
    }
    if not isinstance(state, dict) or set(state) != required or state.get("schema_version") != "skilltree-runtime/v1":
        return None, _check("runtime_state", "fail", "runtime_state_invalid")
    return state, _check("runtime_state", "pass", "ok")


def _plugin_root(state: dict[str, object] | None) -> Path | None:
    if state is None or not isinstance(state.get("plugin_root"), str):
        return None
    path = Path(state["plugin_root"])
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def _versions_check(state: dict[str, object] | None, manifest: dict[str, Any] | None) -> dict[str, str]:
    if state is None or manifest is None:
        return _check("versions", "fail", "version_mismatch")
    try:
        installed_core = version("skilltree-core")
    except PackageNotFoundError:
        return _check("versions", "fail", "core_import_failed")
    expected = (manifest["plugin"]["version"], manifest["core"]["version"], manifest["schema"]["version"])
    actual = (state["plugin_version"], state["core_version"], state["skilltree_schema_version"])
    if actual != expected or installed_core != manifest["core"]["version"] or state["bundle_hash"] != manifest["bundle_hash"]:
        return _check("versions", "fail", "version_mismatch")
    return _check("versions", "pass", "ok")


def _migrations_check(data_dir: Path, manifest: dict[str, Any] | None) -> dict[str, str]:
    path = data_dir / "skilltree.sqlite3"
    if not path.is_file():
        return _check("schema_migrations", "fail", "database_missing")
    if manifest is None:
        return _check("schema_migrations", "fail", "migration_mismatch")
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True) as connection:
            actual = list(connection.execute("SELECT version, content_hash FROM schema_migrations ORDER BY version"))
    except sqlite3.Error:
        return _check("schema_migrations", "fail", "database_unreadable")
    expected = [(item["version"], item["sha256"]) for item in manifest["migrations"]]
    return _check("schema_migrations", "pass", "ok") if actual == expected else _check("schema_migrations", "fail", "migration_mismatch")


def _hook_bundle_check(state: dict[str, object] | None, manifest: dict[str, Any] | None) -> dict[str, str]:
    if manifest is None:
        return _check("hook_bundle", "fail", "hook_bundle_missing")
    if state is None or state["hook_bundle_hash"] != manifest["hook_bundle"]["hash"]:
        return _check("hook_bundle", "fail", "hook_bundle_mismatch")
    return _check("hook_bundle", "pass", "ok")


def _hook_observation_check(
    data_dir: Path, manifest: dict[str, Any] | None,
) -> tuple[dict[str, str], str | None]:
    if manifest is None or manifest["schema"]["migration_version"] < 5:
        return _check("hook_observation", "unknown", "hook_unconfirmed"), None

    database_path = data_dir / "skilltree.sqlite3"
    if not database_path.is_file():
        return _check("hook_observation", "unknown", "hook_unconfirmed"), None

    hook_hash = manifest["hook_bundle"]["hash"]
    try:
        with sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro&immutable=1", uri=True
        ) as connection:
            row = connection.execute(
                "SELECT last_observed_at FROM hook_observations "
                "WHERE hook_bundle_hash = ?",
                (hook_hash,),
            ).fetchone()
    except sqlite3.Error:
        return _check("hook_observation", "unknown", "hook_unconfirmed"), None

    if row is None or not isinstance(row[0], str) or not row[0]:
        return _check("hook_observation", "unknown", "hook_unconfirmed"), None
    try:
        observed_at = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            raise ValueError("observation_timestamp_naive")
        observed_at.astimezone(UTC)
    except (TypeError, ValueError):
        return _check("hook_observation", "unknown", "hook_unconfirmed"), None
    return _check("hook_observation", "pass", "observed"), row[0]


def _bundle_error_code(message: str) -> str:
    if "cannot read" in message:
        return "manifest_missing"
    if "migration" in message:
        return "migration_manifest_invalid"
    if "hash mismatch" in message:
        return "file_hash_mismatch"
    return "manifest_invalid"


def _check(name: str, state: str, code: str) -> dict[str, str]:
    return {"name": name, "state": state, "code": code}
