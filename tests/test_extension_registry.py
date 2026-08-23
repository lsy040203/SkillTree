from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from skilltree.bundle import build_bundle
from skilltree.core.extension_manifest import parse_extension_manifest
from skilltree.core.extension_registry import (
    ExtensionRegistryError,
    list_extensions,
    register_extension,
    remove_extension,
    resolve_task_type,
    set_extension_state,
)
from skilltree.core.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


def _manifest(extension_id: str, task_type: str) -> object:
    value: dict[str, object] = {
        "schema_version": "skilltree-replay-bundle/v2",
        "extension_id": extension_id,
        "extension_version": "1.0.0",
        "adapter": {"name": "adapter", "task_types": [task_type], "task_schemas": {task_type: "schema.json"}},
        "capabilities": {"network": False, "host_workspace": False, "credentials": False, "max_input_bytes": 1024, "max_artifact_bytes": 1024, "timeout_seconds": 30},
        "requires": {"plugin_version_range": ">=0.4.1", "core_version_range": ">=0.4.1", "schema_version": "skilltree/v1"},
        "image": {"name": extension_id + ":1.0.0", "digest": "sha256:" + "a" * 64},
        "oci_archive": {"path": "adapter.tar", "sha256": "sha256:" + "b" * 64},
        "bundle_hash": "",
    }
    unsigned = dict(value)
    unsigned.pop("bundle_hash")
    value["bundle_hash"] = "sha256:" + hashlib.sha256(json.dumps(unsigned, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    return parse_extension_manifest(value, allow_legacy_reference=False)


def _database(tmp_path: Path) -> Database:
    build_bundle(ROOT)
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=9)
    return database


def test_registry_register_is_idempotent_and_resolves(tmp_path: Path) -> None:
    database = _database(tmp_path)
    manifest = _manifest("com.example.one", "com.example.verify")
    first = register_extension(database, manifest)
    second = register_extension(database, manifest)
    assert first.extension_id == second.extension_id
    assert len(list_extensions(database)) == 1
    assert resolve_task_type(database, "com.example.verify").image_digest.startswith("sha256:")


def test_registry_rejects_duplicate_task_owner(tmp_path: Path) -> None:
    database = _database(tmp_path)
    register_extension(database, _manifest("com.example.one", "com.example.verify"))
    with pytest.raises(ExtensionRegistryError, match="task_type_conflict"):
        register_extension(database, _manifest("com.example.two", "com.example.verify"))


def test_registry_state_and_remove_preserve_row(tmp_path: Path) -> None:
    database = _database(tmp_path)
    register_extension(database, _manifest("com.example.one", "com.example.verify"))
    set_extension_state(database, "com.example.one", "disable")
    with pytest.raises(ExtensionRegistryError, match="task_type_unavailable"):
        resolve_task_type(database, "com.example.verify")
    set_extension_state(database, "com.example.one", "enable")
    remove_extension(database, "com.example.one")
    record = list_extensions(database)[0]
    assert record.install_state == "removed"
    with pytest.raises(ExtensionRegistryError, match="task_type_unavailable"):
        resolve_task_type(database, "com.example.verify")
