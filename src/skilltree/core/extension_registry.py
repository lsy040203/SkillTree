"""SQLite registry for explicitly installed Replay Extensions."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from skilltree.core.extension_manifest import ExtensionManifest


class ExtensionRegistryError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ExtensionRecord:
    extension_id: str
    extension_version: str
    adapter_name: str
    task_types: tuple[str, ...]
    manifest_hash: str
    image_name: str
    image_digest: str
    trust_state: str
    install_state: str
    installed_at: str
    updated_at: str

    @property
    def enabled(self) -> bool:
        return self.install_state == "installed" and self.trust_state in {"official", "local_unverified"}


def register_extension(database: Any, manifest: ExtensionManifest, *, trust_state: str = "local_unverified", manifest_hash: str | None = None, image_name: str = "") -> ExtensionRecord:
    """Register an extension atomically; repeated registration is idempotent."""
    if trust_state not in {"official", "local_unverified"}:
        raise ExtensionRegistryError("invalid_state")
    path = _database_path(database)
    now = _now()
    digest = manifest_hash or manifest.bundle_hash
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise ExtensionRegistryError("invalid_schema")
    image = image_name or manifest.extension_id
    try:
        with sqlite3.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT extension_version, adapter_name, task_types_json, manifest_hash, image_name, image_digest, trust_state, install_state, installed_at, updated_at FROM replay_extensions WHERE extension_id = ?",
                (manifest.extension_id,),
            ).fetchone()
            task_json = json.dumps(list(manifest.task_types), separators=(",", ":"), sort_keys=True)
            if existing is not None:
                if existing[3] != digest or existing[0] != manifest.extension_version:
                    raise ExtensionRegistryError("extension_conflict")
                connection.execute(
                    "UPDATE replay_extensions SET trust_state = ?, install_state = 'installed', updated_at = ? WHERE extension_id = ?",
                    (trust_state, now, manifest.extension_id),
                )
            else:
                owners = connection.execute(
                    "SELECT DISTINCT extension_id FROM replay_extensions, json_each(replay_extensions.task_types_json) WHERE install_state = 'installed' AND trust_state IN ('official','local_unverified') AND json_each.value IN (%s)" % ",".join("?" for _ in manifest.task_types),
                    tuple(manifest.task_types),
                ).fetchall()
                if owners:
                    raise ExtensionRegistryError("task_type_conflict")
                connection.execute(
                    "INSERT INTO replay_extensions(extension_id, extension_version, adapter_name, task_types_json, manifest_hash, image_name, image_digest, trust_state, install_state, installed_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (manifest.extension_id, manifest.extension_version, manifest.adapter_name, task_json, digest, image or manifest.image_name, manifest.image_digest, trust_state, "installed", now, now),
                )
            row = connection.execute("SELECT extension_id, extension_version, adapter_name, task_types_json, manifest_hash, image_name, image_digest, trust_state, install_state, installed_at, updated_at FROM replay_extensions WHERE extension_id = ?", (manifest.extension_id,)).fetchone()
            connection.commit()
    except ExtensionRegistryError:
        raise
    except sqlite3.Error as error:
        raise ExtensionRegistryError("internal_error") from error
    return _record(row)


def set_extension_state(database: Any, extension_id: str, state: str) -> ExtensionRecord:
    if state not in {"enable", "disable", "revoke"}:
        raise ExtensionRegistryError("invalid_state")
    column_value = {"enable": ("trust_state", "local_unverified", "installed"), "disable": ("trust_state", "disabled", "installed"), "revoke": ("trust_state", "revoked", "installed")} [state]
    path = _database_path(database)
    try:
        with sqlite3.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM replay_extensions WHERE extension_id = ?", (extension_id,)).fetchone() is None:
                raise ExtensionRegistryError("not_found")
            connection.execute("UPDATE replay_extensions SET trust_state = ?, install_state = ?, updated_at = ? WHERE extension_id = ?", (column_value[1], column_value[2], _now(), extension_id))
            row = connection.execute("SELECT extension_id, extension_version, adapter_name, task_types_json, manifest_hash, image_name, image_digest, trust_state, install_state, installed_at, updated_at FROM replay_extensions WHERE extension_id = ?", (extension_id,)).fetchone()
            connection.commit()
    except ExtensionRegistryError:
        raise
    except sqlite3.Error as error:
        raise ExtensionRegistryError("internal_error") from error
    return _record(row)


def remove_extension(database: Any, extension_id: str) -> None:
    path = _database_path(database)
    try:
        with sqlite3.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM replay_extensions WHERE extension_id = ?", (extension_id,)).fetchone() is None:
                raise ExtensionRegistryError("not_found")
            connection.execute("UPDATE replay_extensions SET install_state = 'removed', trust_state = 'disabled', updated_at = ? WHERE extension_id = ?", (_now(), extension_id))
            connection.commit()
    except ExtensionRegistryError:
        raise
    except sqlite3.Error as error:
        raise ExtensionRegistryError("internal_error") from error


def list_extensions(database: Any) -> list[ExtensionRecord]:
    path = _database_path(database)
    try:
        with sqlite3.connect(path) as connection:
            rows = connection.execute("SELECT extension_id, extension_version, adapter_name, task_types_json, manifest_hash, image_name, image_digest, trust_state, install_state, installed_at, updated_at FROM replay_extensions ORDER BY extension_id").fetchall()
    except sqlite3.Error as error:
        raise ExtensionRegistryError("internal_error") from error
    records = [_record(row) for row in rows]
    if not records:
        records.extend(_legacy_runtime_records(path))
    return records


def resolve_task_type(database: Any, task_type: str) -> ExtensionRecord:
    matches = [record for record in list_extensions(database) if record.enabled and task_type in record.task_types]
    if not matches:
        raise ExtensionRegistryError("task_type_unavailable")
    if len(matches) != 1:
        raise ExtensionRegistryError("task_type_ambiguous")
    return matches[0]


def _database_path(database: Any) -> str | Path:
    path = getattr(database, "path", database)
    if not isinstance(path, (str, Path)):
        raise ExtensionRegistryError("invalid_schema")
    return path


def _record(row: tuple[Any, ...] | None) -> ExtensionRecord:
    if row is None:
        raise ExtensionRegistryError("not_found")
    return ExtensionRecord(row[0], row[1], row[2], tuple(json.loads(row[3])), row[4], row[5], row[6], row[7], row[8], row[9], row[10])


def _legacy_runtime_records(database_path: str | Path) -> list[ExtensionRecord]:
    state_path = Path(database_path).parent / "replay-runtime-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(state, dict) or state.get("schema_version") != "skilltree-replay-runtime/v1":
        return []
    now = str(state.get("installed_at", _now()))
    return [ExtensionRecord(
        "org.skilltree.reference", str(state.get("extension_version", "1.0.0")), "reference",
        ("org.skilltree.python.repository_verification", "repository_verification"),
        str(state.get("extension_bundle_hash", "")), str(state.get("image_name", "")), str(state.get("image_digest", "")),
        "official", "installed", now, now,
    )]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
