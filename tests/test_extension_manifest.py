from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from skilltree.core.adapter_contracts import AdapterContractError, AdapterResult, TaskRequest, validate_adapter_result, validate_task_request
from skilltree.core.extension_manifest import ExtensionManifestError, parse_extension_manifest


def _manifest(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "skilltree-replay-bundle/v2",
        "extension_id": "com.example.python-replay",
        "extension_version": "1.0.0",
        "adapter": {
            "name": "python-repository",
            "task_types": ["com.example.python.repository_verification"],
            "task_schemas": {"com.example.python.repository_verification": "schemas/repository.json"},
        },
        "capabilities": {
            "network": False,
            "host_workspace": False,
            "credentials": False,
            "max_input_bytes": 1024,
            "max_artifact_bytes": 1024,
            "timeout_seconds": 30,
        },
        "requires": {
            "plugin_version_range": ">=0.4.1,<0.5.0",
            "core_version_range": "==0.4.1",
            "schema_version": "skilltree/v1",
        },
        "image": {"name": "example:1.0.0", "digest": "sha256:" + "a" * 64},
        "oci_archive": {"path": "example-1.0.0.oci.tar", "sha256": "sha256:" + "b" * 64},
        "bundle_hash": "",
    }
    value.update(overrides)
    if value["bundle_hash"] == "":
        unsigned = dict(value)
        unsigned.pop("bundle_hash", None)
        value["bundle_hash"] = "sha256:" + hashlib.sha256(
            json.dumps(unsigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
    return value


def test_v2_manifest_parses_with_namespaced_task_type() -> None:
    manifest = parse_extension_manifest(_manifest(), allow_legacy_reference=False)
    assert manifest.extension_id == "com.example.python-replay"
    assert manifest.task_types == ("com.example.python.repository_verification",)
    assert manifest.capabilities.network is False


def test_v2_manifest_rejects_unknown_fields() -> None:
    value = _manifest()
    value["unknown"] = True
    with pytest.raises(ExtensionManifestError, match="invalid_schema"):
        parse_extension_manifest(value, allow_legacy_reference=False)


def test_v2_manifest_rejects_unsafe_capability() -> None:
    value = _manifest()
    value["capabilities"] = {**value["capabilities"], "network": True}
    with pytest.raises(ExtensionManifestError, match="capability_rejected"):
        parse_extension_manifest(value, allow_legacy_reference=False)


def test_v2_manifest_rejects_noncanonical_identity_path_or_hash() -> None:
    bad_identifier = _manifest(extension_id="python")
    with pytest.raises(ExtensionManifestError, match="invalid_schema"):
        parse_extension_manifest(bad_identifier, allow_legacy_reference=False)
    bad_archive = _manifest(oci_archive={"path": "../runner.tar", "sha256": "sha256:" + "b" * 64})
    with pytest.raises(ExtensionManifestError, match="invalid_schema"):
        parse_extension_manifest(bad_archive, allow_legacy_reference=False)
    bad_hash = _manifest(bundle_hash="sha256:" + "0" * 64)
    with pytest.raises(ExtensionManifestError, match="manifest_hash_mismatch"):
        parse_extension_manifest(bad_hash, allow_legacy_reference=False)


def test_task_request_rejects_command_and_accepts_bounded_contract() -> None:
    request = validate_task_request({
        "schema_version": "skilltree-replay-task/v1",
        "episode_id": "episode-1",
        "arm": "baseline",
        "task_type": "com.example.python.repository_verification",
        "fixture": {"source": "lesson.py"},
        "asset_snapshot": {},
    })
    assert isinstance(request, TaskRequest)
    with pytest.raises(AdapterContractError, match="invalid_schema"):
        validate_task_request({
            "schema_version": "skilltree-replay-task/v1",
            "episode_id": "episode-1",
            "arm": "baseline",
            "task_type": "com.example.python.repository_verification",
            "fixture": {"command": "rm -rf /"},
            "asset_snapshot": {},
        })


def test_adapter_result_requires_matching_episode_and_arm() -> None:
    value = validate_adapter_result({
        "schema_version": "skilltree/v1",
        "episode_id": "episode-1",
        "arm": "candidate",
        "verdict": "success",
        "quality_score": 1.0,
        "latency_ms": 5,
        "error_code": None,
        "guardrail_breaches": [],
        "artifact_refs": [],
    }, episode_id="episode-1", arm="candidate")
    assert isinstance(value, AdapterResult)
    with pytest.raises(AdapterContractError, match="result_invalid"):
        validate_adapter_result({
            "schema_version": "skilltree/v1", "episode_id": "other", "arm": "candidate",
            "verdict": "success", "quality_score": 1.0, "latency_ms": 5,
            "guardrail_breaches": [], "artifact_refs": [],
        }, episode_id="episode-1", arm="candidate")
