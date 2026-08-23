from __future__ import annotations

import hashlib
import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from skilltree.core.replay_extension import ReplayExtensionError, install_extension, replay_diagnose, uninstall_extension


def _bundle(tmp_path: Path, *, digest: str = "sha256:" + "a" * 64) -> Path:
    root = tmp_path / "replay-extension"
    root.mkdir(parents=True)
    archive = root / "skilltree-replay-runner-1.0.0.oci.tar"
    archive.write_bytes(b"offline-image")
    manifest = {
        "schema_version": "skilltree-replay-bundle/v1",
        "extension_version": "1.0.0",
        "requires": {"plugin_version_range": ">=0.4.1", "core_version_range": ">=0.4.1", "schema_version": "skilltree/v1"},
        "oci_archive": {"path": archive.name, "sha256": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()},
        "image": {"name": "skilltree-replay-runner:1.0.0", "digest": digest},
    }
    unsigned = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    manifest["bundle_hash"] = "sha256:" + hashlib.sha256(unsigned).hexdigest()
    (root / "replay-bundle-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


class FakeDocker:
    def __init__(self, digest: str):
        self.digest = digest
        self.calls: list[list[str]] = []
        self.fail_load = False

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[1:3] == ["version", "--format"]:
            return CompletedProcess(args, 0, "26.0|26.0", "")
        if args[1:3] == ["load", "--input"]:
            return CompletedProcess(args, 1 if self.fail_load else 0, "", "")
        if args[1:3] == ["image", "inspect"]:
            return CompletedProcess(args, 0, json.dumps(["skilltree-replay-runner:1.0.0@" + self.digest]), "")
        if args[1:3] == ["image", "rm"]:
            return CompletedProcess(args, 0, "", "")
        return CompletedProcess(args, 1, "", "")


def _docker_path(tmp_path: Path) -> Path:
    path = tmp_path / "docker.exe"
    path.write_bytes(b"stub")
    return path


def test_install_validates_offline_bundle_and_writes_redacted_state(tmp_path: Path) -> None:
    digest = "sha256:" + "a" * 64
    docker = FakeDocker(digest)
    result = install_extension(tmp_path / "data", _bundle(tmp_path, digest=digest), docker_path=_docker_path(tmp_path), runner=docker)
    assert result["extension_version"] == "1.0.0"
    state = json.loads((tmp_path / "data" / "replay-runtime-state.json").read_text())
    assert set(state) == {"schema_version", "extension_bundle_hash", "extension_version", "runtime_path", "image_name", "image_digest", "installed_at"}
    assert str(tmp_path) not in json.dumps(state)


def test_install_failure_preserves_previous_state(tmp_path: Path) -> None:
    digest = "sha256:" + "a" * 64
    data = tmp_path / "data"
    docker = FakeDocker(digest)
    install_extension(data, _bundle(tmp_path, digest=digest), docker_path=_docker_path(tmp_path), runner=docker)
    before = (data / "replay-runtime-state.json").read_text()
    new_digest = "sha256:" + "b" * 64
    docker.digest = new_digest
    docker.fail_load = True
    with pytest.raises(ReplayExtensionError, match="replay_runtime_unavailable"):
        install_extension(data, _bundle(tmp_path / "new", digest=new_digest), docker_path=_docker_path(tmp_path), runner=docker)
    assert (data / "replay-runtime-state.json").read_text() == before


def test_install_is_idempotent_for_same_bundle(tmp_path: Path) -> None:
    digest = "sha256:" + "a" * 64
    docker = FakeDocker(digest)
    root = _bundle(tmp_path, digest=digest)
    data = tmp_path / "data"
    install_extension(data, root, docker_path=_docker_path(tmp_path), runner=docker)
    calls = len(docker.calls)
    result = install_extension(data, root, docker_path=_docker_path(tmp_path), runner=docker)
    assert result["extension_bundle_hash"]
    assert len(docker.calls) == calls


def test_uninstall_removes_state_but_not_other_data(tmp_path: Path) -> None:
    digest = "sha256:" + "a" * 64
    docker = FakeDocker(digest)
    data = tmp_path / "data"
    install_extension(data, _bundle(tmp_path, digest=digest), docker_path=_docker_path(tmp_path), runner=docker)
    marker = data / "skilltree.sqlite3"
    marker.write_bytes(b"keep")
    result = uninstall_extension(data, docker_path=_docker_path(tmp_path), runner=docker)
    assert result["removed_image_digest"] == digest
    assert not (data / "replay-runtime-state.json").exists()
    assert marker.exists()


def test_doctor_replay_is_fail_closed_when_uninstalled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SKILLTREE_DOCKER_PATH", raising=False)
    payload, code = replay_diagnose(tmp_path / "data", docker_path=None)
    assert code == 2
    assert payload["replay_ready"] is False
    assert {item["code"] for item in payload["replay_checks"]} >= {"replay_runtime_path_invalid", "replay_runtime_state_missing"}


def test_replay_request_parser_rejects_relative_or_unknown_fields(tmp_path: Path) -> None:
    from skilltree.interfaces.replay_io import ReplayInputError, load_replay_request

    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": "skilltree/v1", "user_id": "local", "confirm": "INSTALL_REPLAY_EXTENSION", "extension_root": str(tmp_path), "extra": 1}), encoding="utf-8")
    with pytest.raises(ReplayInputError, match="invalid_schema"):
        load_replay_request(request, "install-extension")
