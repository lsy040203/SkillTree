from __future__ import annotations

from pathlib import Path

from skilltree.bundle import build_bundle
from skilltree.core.extension_registry import ExtensionRegistryError, register_extension, resolve_task_type, set_extension_state
from skilltree.core.storage import Database
from skilltree.core.extension_manifest import parse_extension_manifest
import hashlib
import json


def _manifest(extension_id: str, task_type: str):
    value = {"schema_version": "skilltree-replay-bundle/v2", "extension_id": extension_id, "extension_version": "1.0.0", "adapter": {"name": "adapter", "task_types": [task_type], "task_schemas": {task_type: "schema.json"}}, "capabilities": {"network": False, "host_workspace": False, "credentials": False, "max_input_bytes": 1024, "max_artifact_bytes": 1024, "timeout_seconds": 30}, "requires": {"plugin_version_range": ">=0.4.1", "core_version_range": ">=0.4.1", "schema_version": "skilltree/v1"}, "image": {"name": extension_id + ":1.0.0", "digest": "sha256:" + "a" * 64}, "oci_archive": {"path": "adapter.tar", "sha256": "sha256:" + "b" * 64}, "bundle_hash": ""}
    unsigned = dict(value); unsigned.pop("bundle_hash")
    value["bundle_hash"] = "sha256:" + hashlib.sha256(json.dumps(unsigned, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    return parse_extension_manifest(value, allow_legacy_reference=False)


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


def test_two_extensions_resolve_and_disable_fail_closed(tmp_path: Path) -> None:
    build_bundle(ROOT)
    database = Database(tmp_path / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=9)
    first = _manifest("com.example.one", "com.example.verify")
    second = _manifest("com.example.two", "com.example.analyze")
    register_extension(database, first)
    register_extension(database, second)
    assert resolve_task_type(database, "com.example.verify").extension_id == "com.example.one"
    assert resolve_task_type(database, "com.example.analyze").extension_id == "com.example.two"
    set_extension_state(database, "com.example.one", "disable")
    try:
        resolve_task_type(database, "com.example.verify")
    except ExtensionRegistryError as error:
        assert error.code == "task_type_unavailable"
    else:
        raise AssertionError("disabled extension must fail closed")
