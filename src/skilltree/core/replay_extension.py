"""Offline, digest-pinned Replay Extension lifecycle for P6."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?\Z")
STATE_SCHEMA = "skilltree-replay-runtime/v1"
MANIFEST_SCHEMA = "skilltree-replay-bundle/v1"


class ReplayExtensionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def install_extension(
    data_dir: Path,
    extension_root: Path,
    *,
    plugin_root: Path | None = None,
    plugin_version: str = "0.4.1+codex.20260821163152",
    core_version: str = "0.4.1",
    docker_path: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    """Validate and load an offline bundle, atomically replacing runtime state."""
    data_dir = data_dir.expanduser().resolve()
    extension_root = _validate_root(extension_root, data_dir, plugin_root)
    manifest = _load_manifest(extension_root)
    _validate_manifest(manifest, extension_root, plugin_version, core_version)
    archive = (extension_root / manifest["oci_archive"]["path"]).resolve()
    old_state = _read_state(data_dir)
    if old_state and old_state["extension_bundle_hash"] == manifest["bundle_hash"]:
        return _result(manifest)
    docker = _controlled_docker(docker_path)
    _docker_available(docker, runner)

    try:
        loaded = _run_docker(docker, ["load", "--input", str(archive)], runner)
        if loaded.returncode != 0:
            raise ReplayExtensionError("replay_runtime_unavailable")
        inspected = _run_docker(
            docker, ["image", "inspect", manifest["image"]["name"], "--format", "{{json .RepoDigests}}"], runner,
        )
        if inspected.returncode != 0 or not _digest_present(inspected.stdout, manifest["image"]["digest"]):
            raise ReplayExtensionError("replay_runtime_unavailable")
        state = {
            "schema_version": STATE_SCHEMA,
            "extension_bundle_hash": manifest["bundle_hash"],
            "extension_version": manifest["extension_version"],
            "runtime_path": "replay-extension",
            "image_name": manifest["image"]["name"],
            "image_digest": manifest["image"]["digest"],
            "installed_at": _now(),
        }
        _atomic_write(data_dir / "replay-runtime-state.json", state)
    except ReplayExtensionError:
        raise
    except (OSError, subprocess.SubprocessError):
        raise ReplayExtensionError("replay_runtime_unavailable") from None
    return _result(manifest)


def uninstall_extension(
    data_dir: Path,
    *,
    docker_path: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    data_dir = data_dir.expanduser().resolve()
    state = _read_state(data_dir)
    if state is None:
        raise ReplayExtensionError("not_found")
    docker = _controlled_docker(docker_path)
    try:
        result = _run_docker(docker, ["image", "rm", f"{state['image_name']}@{state['image_digest']}"], runner)
        if result.returncode != 0:
            raise ReplayExtensionError("internal_error")
        (data_dir / "replay-runtime-state.json").unlink()
    except ReplayExtensionError:
        raise
    except (OSError, subprocess.SubprocessError):
        raise ReplayExtensionError("internal_error") from None
    return {"removed_image_digest": state["image_digest"], "completed_at": _now()}


def replay_diagnose(data_dir: Path, *, docker_path: Path | None = None, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> tuple[dict[str, object], int]:
    """Read-only replay readiness checks; no image load, pull, or container run."""
    checks: list[dict[str, str]] = []
    docker = _controlled_docker(docker_path, allow_missing=True)
    if docker is None or not docker.is_file():
        checks.append({"name": "replay_runtime_path", "state": "fail", "code": "replay_runtime_path_invalid"})
    else:
        try:
            result = _run_docker(docker, ["version", "--format", "{{.Client.Version}}|{{.Server.Version}}"], runner)
        except (OSError, subprocess.SubprocessError):
            result = None
        checks.append({"name": "replay_runtime", "state": "pass" if result and result.returncode == 0 else "fail", "code": "ok" if result and result.returncode == 0 else "replay_runtime_unavailable"})
    state = _read_state(data_dir)
    if state is None:
        checks.append({"name": "replay_runtime_state", "state": "fail", "code": "replay_runtime_state_missing"})
        checks.extend([
            {"name": "replay_image", "state": "fail", "code": "replay_image_missing"},
            {"name": "replay_image_digest", "state": "fail", "code": "replay_image_digest_mismatch"},
        ])
    else:
        checks.append({"name": "replay_runtime_state", "state": "pass", "code": "ok"})
        image_result = _run_docker(docker, ["image", "inspect", state["image_name"]], runner) if docker else None
        image_ok = bool(image_result and image_result.returncode == 0)
        checks.append({"name": "replay_image", "state": "pass" if image_ok else "fail", "code": "ok" if image_ok else "replay_image_missing"})
        digest_result = _run_docker(docker, ["image", "inspect", state["image_name"], "--format", "{{json .RepoDigests}}"], runner) if docker else None
        digest_ok = bool(digest_result and digest_result.returncode == 0 and _digest_present(digest_result.stdout, state["image_digest"]))
        checks.append({"name": "replay_image_digest", "state": "pass" if digest_ok else "fail", "code": "ok" if digest_ok else "replay_image_digest_mismatch"})
    ready = all(check["state"] == "pass" for check in checks)
    return {"schema_version": "skilltree-doctor/v1", "replay_ready": ready, "replay_checks": checks}, 0 if ready else 2


def _load_manifest(root: Path) -> dict[str, Any]:
    path = root / "replay-bundle-manifest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReplayExtensionError("out_of_scope") from None
    if not isinstance(value, dict):
        raise ReplayExtensionError("out_of_scope")
    return value


def _validate_manifest(manifest: dict[str, Any], root: Path, plugin_version: str, core_version: str) -> None:
    if manifest.get("schema_version") == "skilltree-replay-bundle/v2":
        _validate_manifest_v2(manifest, root)
        return
    required = {"schema_version", "extension_version", "requires", "oci_archive", "image", "bundle_hash"}
    if set(manifest) != required or manifest["schema_version"] != MANIFEST_SCHEMA:
        raise ReplayExtensionError("out_of_scope")
    if not isinstance(manifest["extension_version"], str) or not _VERSION.fullmatch(manifest["extension_version"]):
        raise ReplayExtensionError("out_of_scope")
    req = manifest["requires"]
    archive = manifest["oci_archive"]
    image = manifest["image"]
    if not isinstance(req, dict) or set(req) != {"plugin_version_range", "core_version_range", "schema_version"} or req["schema_version"] != "skilltree/v1":
        raise ReplayExtensionError("out_of_scope")
    if not _version_satisfies(plugin_version, req["plugin_version_range"]) or not _version_satisfies(core_version, req["core_version_range"]):
        raise ReplayExtensionError("authorization_required")
    if not isinstance(archive, dict) or set(archive) != {"path", "sha256"} or not isinstance(archive["path"], str) or not _DIGEST.fullmatch(archive["sha256"]):
        raise ReplayExtensionError("out_of_scope")
    archive_path = (root / archive["path"]).resolve()
    expected_archive = f"skilltree-replay-runner-{manifest['extension_version']}.oci.tar"
    if (
        archive["path"] != expected_archive
        or archive_path.parent != root
        or archive_path.is_symlink()
        or not archive_path.is_file()
        or _sha256(archive_path) != archive["sha256"]
    ):
        raise ReplayExtensionError("replay_runtime_unavailable")
    if not isinstance(image, dict) or set(image) != {"name", "digest"} or not isinstance(image["name"], str) or not _DIGEST.fullmatch(image["digest"]):
        raise ReplayExtensionError("out_of_scope")
    if not _DIGEST.fullmatch(manifest["bundle_hash"]):
        raise ReplayExtensionError("out_of_scope")
    unsigned = dict(manifest)
    unsigned.pop("bundle_hash")
    canonical = json.dumps(unsigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if "sha256:" + hashlib.sha256(canonical).hexdigest() != manifest["bundle_hash"]:
        raise ReplayExtensionError("replay_runtime_unavailable")


def _validate_manifest_v2(manifest: dict[str, Any], root: Path) -> None:
    """Validate the v2 manifest using the shared strict Adapter contract."""
    from skilltree.core.extension_manifest import ExtensionManifestError, parse_extension_manifest

    try:
        parsed = parse_extension_manifest(root / "replay-bundle-manifest.json", allow_legacy_reference=False)
    except ExtensionManifestError as error:
        raise ReplayExtensionError("out_of_scope" if error.args and error.args[0] in {"invalid_schema", "invalid_task_type", "capability_rejected", "legacy_manifest_rejected"} else "replay_runtime_unavailable") from error
    archive = (root / manifest["oci_archive"]["path"]).resolve()
    if archive.parent != root or archive.is_symlink() or not archive.is_file() or _sha256(archive) != manifest["oci_archive"]["sha256"]:
        raise ReplayExtensionError("replay_runtime_unavailable")
    if parsed.bundle_hash != manifest.get("bundle_hash") or parsed.image_digest != manifest.get("image", {}).get("digest"):
        raise ReplayExtensionError("replay_runtime_unavailable")


def _version_satisfies(version: str, constraint: object) -> bool:
    if not isinstance(constraint, str) or not constraint:
        return False
    base = tuple(int(x) for x in re.match(r"^(\d+)\.(\d+)\.(\d+)", version).groups())
    for part in constraint.split(","):
        match = re.fullmatch(r"\s*(>=|<=|>|<|==)?\s*(\d+)\.(\d+)\.(\d+)\s*", part)
        if not match:
            return False
        op = match.group(1) or "=="
        target = tuple(int(match.group(i)) for i in (2, 3, 4))
        if not {">=": base >= target, "<=": base <= target, ">": base > target, "<": base < target, "==": base == target}[op]:
            return False
    return True


def _validate_root(root: Path, data_dir: Path, plugin_root: Path | None) -> Path:
    if root.is_symlink():
        raise ReplayExtensionError("out_of_scope")
    try:
        resolved = root.expanduser().resolve(strict=True)
    except OSError:
        raise ReplayExtensionError("out_of_scope") from None
    if resolved.name != "replay-extension":
        raise ReplayExtensionError("out_of_scope")
    forbidden = [data_dir, Path.cwd().resolve()]
    if plugin_root is not None:
        forbidden.append(plugin_root.expanduser().resolve())
    if any(
        resolved == forbidden_path
        or resolved in forbidden_path.parents
        or forbidden_path in resolved.parents
        for forbidden_path in forbidden
    ):
        raise ReplayExtensionError("out_of_scope")
    return resolved


def _controlled_docker(path: Path | None, *, allow_missing: bool = False) -> Path | None:
    candidate = path or (Path(os.environ["SKILLTREE_DOCKER_PATH"]) if os.environ.get("SKILLTREE_DOCKER_PATH") else None)
    if candidate is None:
        if allow_missing:
            return None
        raise ReplayExtensionError("replay_runtime_unavailable")
    candidate = candidate.expanduser().resolve()
    if not candidate.is_file():
        if allow_missing:
            return candidate
        raise ReplayExtensionError("replay_runtime_unavailable")
    return candidate


def _docker_available(docker: Path, runner: Callable[..., subprocess.CompletedProcess[str]] | None) -> None:
    result = _run_docker(docker, ["version", "--format", "{{.Client.Version}}|{{.Server.Version}}"], runner)
    if result.returncode != 0 or not result.stdout.strip() or "|" not in result.stdout:
        raise ReplayExtensionError("replay_runtime_unavailable")


def _run_docker(docker: Path, args: list[str], runner: Callable[..., subprocess.CompletedProcess[str]] | None) -> subprocess.CompletedProcess[str]:
    call = runner or subprocess.run
    return call([str(docker), *args], capture_output=True, text=True, check=False, timeout=30)


def _digest_present(output: str, digest: str) -> bool:
    return digest in output


def _read_state(data_dir: Path) -> dict[str, str] | None:
    path = data_dir / "replay-runtime-state.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    expected = {"schema_version", "extension_bundle_hash", "extension_version", "runtime_path", "image_name", "image_digest", "installed_at"}
    return value if isinstance(value, dict) and set(value) == expected and value.get("schema_version") == STATE_SCHEMA and _DIGEST.fullmatch(value.get("extension_bundle_hash", "")) and _DIGEST.fullmatch(value.get("image_digest", "")) else None


def _atomic_write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _result(manifest: dict[str, Any]) -> dict[str, object]:
    return {"extension_version": manifest["extension_version"], "extension_bundle_hash": manifest["bundle_hash"], "image_digest": manifest["image"]["digest"], "completed_at": _now()}


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
