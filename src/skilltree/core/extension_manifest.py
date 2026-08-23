"""Parser and policy validator for Replay Extension manifests."""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ExtensionManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Capabilities:
    network: bool
    host_workspace: bool
    credentials: bool
    max_input_bytes: int
    max_artifact_bytes: int
    timeout_seconds: int


@dataclass(frozen=True)
class ExtensionManifest:
    extension_id: str
    extension_version: str
    adapter_name: str
    task_types: tuple[str, ...]
    task_schemas: dict[str, str]
    capabilities: Capabilities
    image_name: str = ""
    image_digest: str = ""
    bundle_hash: str = ""


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_NAMESPACE = re.compile(r"(?:org\.skilltree|[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+)\.[a-z0-9_][a-z0-9_.-]*\Z")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+\Z")
_VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\Z")
_TOP = {"schema_version", "extension_id", "extension_version", "adapter", "capabilities", "requires", "image", "oci_archive", "bundle_hash"}


def parse_extension_manifest(value: object, *, allow_legacy_reference: bool) -> ExtensionManifest:
    if isinstance(value, Path):
        try:
            value = json.loads(value.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ExtensionManifestError("invalid_schema") from None
    if not isinstance(value, dict):
        raise ExtensionManifestError("invalid_schema")
    if value.get("schema_version") == "skilltree-replay-bundle/v1":
        if not allow_legacy_reference:
            raise ExtensionManifestError("legacy_manifest_rejected")
        return ExtensionManifest("org.skilltree.reference", str(value.get("extension_version", "")), "reference", ("org.skilltree.python.repository_verification",), {}, Capabilities(False, False, False, 1024 * 1024, 1024 * 1024, 30), str(value.get("image", {}).get("name", "")), str(value.get("image", {}).get("digest", "")), str(value.get("bundle_hash", "")))
    if value.get("schema_version") != "skilltree-replay-bundle/v2" or set(value) != _TOP:
        raise ExtensionManifestError("invalid_schema")
    adapter = value.get("adapter")
    caps = value.get("capabilities")
    if not isinstance(adapter, dict) or set(adapter) != {"name", "task_types", "task_schemas"} or not isinstance(caps, dict) or set(caps) != {"network", "host_workspace", "credentials", "max_input_bytes", "max_artifact_bytes", "timeout_seconds"}:
        raise ExtensionManifestError("invalid_schema")
    task_types = adapter["task_types"]
    schemas = adapter["task_schemas"]
    if not isinstance(task_types, list) or not task_types or not all(isinstance(item, str) and _NAMESPACE.fullmatch(item) for item in task_types):
        raise ExtensionManifestError("invalid_task_type")
    if not isinstance(schemas, dict) or set(schemas) != set(task_types) or not all(isinstance(item, str) and item for item in schemas.values()):
        raise ExtensionManifestError("invalid_schema")
    if caps["network"] or caps["host_workspace"] or caps["credentials"]:
        raise ExtensionManifestError("capability_rejected")
    limits = [caps[key] for key in ("max_input_bytes", "max_artifact_bytes", "timeout_seconds")]
    if not all(isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in limits) or caps["max_input_bytes"] > 16 * 1024 * 1024 or caps["max_artifact_bytes"] > 16 * 1024 * 1024 or caps["timeout_seconds"] > 300:
        raise ExtensionManifestError("capability_rejected")
    if not isinstance(value.get("extension_id"), str) or not _IDENTIFIER.fullmatch(value["extension_id"]) or not isinstance(value.get("extension_version"), str) or not _VERSION.fullmatch(value["extension_version"]) or not isinstance(adapter.get("name"), str) or not adapter["name"]:
        raise ExtensionManifestError("invalid_schema")
    for section, keys in ((value["image"], {"name", "digest"}), (value["oci_archive"], {"path", "sha256"}), (value["requires"], {"plugin_version_range", "core_version_range", "schema_version"})):
        if not isinstance(section, dict) or set(section) != keys:
            raise ExtensionManifestError("invalid_schema")
    archive_path = value["oci_archive"]["path"]
    if not isinstance(archive_path, str) or not archive_path or "\\" in archive_path or Path(archive_path).is_absolute() or ".." in Path(archive_path).parts:
        raise ExtensionManifestError("invalid_schema")
    if not _DIGEST.fullmatch(value["image"]["digest"]) or not _DIGEST.fullmatch(value["oci_archive"]["sha256"]) or value["requires"]["schema_version"] != "skilltree/v1" or not _DIGEST.fullmatch(value["bundle_hash"]):
        raise ExtensionManifestError("invalid_schema")
    unsigned = dict(value)
    unsigned.pop("bundle_hash")
    canonical = json.dumps(unsigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if "sha256:" + hashlib.sha256(canonical).hexdigest() != value["bundle_hash"]:
        raise ExtensionManifestError("manifest_hash_mismatch")
    return ExtensionManifest(value["extension_id"], value["extension_version"], adapter["name"], tuple(task_types), schemas, Capabilities(**caps), value["image"]["name"], value["image"]["digest"], value["bundle_hash"])
