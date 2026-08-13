"""Build and verify the immutable P0 SkillTree Plugin bundle."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import tempfile
import base64
import csv
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


BUNDLE_SCHEMA_VERSION = "skilltree-bundle/v1"
CORE_DISTRIBUTION = "skilltree-core"
SCHEMA_VERSION = "skilltree/v1"
_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MIGRATION_PATTERN = re.compile(r"migrations/(\d{4})_[a-z0-9_]+\.sql\Z")


class BundleValidationError(ValueError):
    """Raised when a release Bundle violates the offline contract."""


def build_bundle(repository_root: Path) -> dict[str, Any]:
    """Build P0 release artifacts in a temporary directory then publish them."""
    repository_root = repository_root.resolve()
    plugin_root = repository_root / "plugins" / "skilltree"
    project = _read_json(repository_root / "pyproject.toml", toml=True)
    version = project["project"]["version"]
    plugin_manifest = _read_json(plugin_root / ".codex-plugin" / "plugin.json")
    if plugin_manifest.get("name") != "skilltree" or plugin_manifest.get("version") != version:
        raise BundleValidationError("plugin/core version mismatch")

    with tempfile.TemporaryDirectory(prefix="skilltree-bundle-") as temp_dir:
        temp_root = Path(temp_dir)
        wheel_dir = temp_root / "wheels"
        wheel_dir.mkdir()
        _build_core_wheel(repository_root, wheel_dir)
        wheel = _find_core_wheel(wheel_dir, version)
        migration = plugin_root / "migrations" / "0001_p0_runtime.sql"
        if not migration.is_file():
            raise BundleValidationError("P0 migration is missing")
        destination_wheels = plugin_root / "runtime" / "wheels"
        destination_wheels.mkdir(parents=True, exist_ok=True)
        for existing in destination_wheels.glob("*"):
            if existing.is_file():
                existing.unlink()
        shutil.copy2(wheel, destination_wheels / wheel.name)

        requirements = f"{CORE_DISTRIBUTION}=={version} --hash={_sha256(wheel)}\n"
        (plugin_root / "requirements.lock").write_text(requirements, encoding="utf-8", newline="\n")
        manifest = _make_manifest(plugin_root, version, wheel.name)
        _write_manifest(plugin_root / "runtime" / "bundle-manifest.json", manifest)

    return validate_bundle(plugin_root)


def validate_bundle(plugin_root: Path) -> dict[str, Any]:
    """Validate all P0 offline artifacts without installing or executing them."""
    plugin_root = plugin_root.resolve()
    manifest_path = plugin_root / "runtime" / "bundle-manifest.json"
    manifest = _read_json(manifest_path)
    _require_exact_keys(
        manifest,
        {
            "schema_version", "plugin", "core", "schema", "migrations", "requirements_lock",
            "runtime_files", "wheels", "hook_bundle", "bundle_hash",
        },
        "bundle manifest",
    )
    if manifest["schema_version"] != BUNDLE_SCHEMA_VERSION:
        raise BundleValidationError("unsupported bundle schema")
    _validate_plugin_and_core(plugin_root, manifest)
    _validate_migrations(plugin_root, manifest)
    _validate_requirements(plugin_root, manifest)
    _validate_runtime_files(plugin_root, manifest)
    _validate_wheels(plugin_root, manifest)
    _validate_hook_bundle(plugin_root, manifest)
    if manifest["bundle_hash"] != _bundle_hash(manifest):
        raise BundleValidationError("bundle hash mismatch")
    return manifest


def _make_manifest(plugin_root: Path, version: str, wheel_filename: str) -> dict[str, Any]:
    wheel_path = Path("runtime") / "wheels" / wheel_filename
    migration_path = "migrations/0001_p0_runtime.sql"
    runtime_paths = sorted(
        [
            "runtime/skilltree_bootstrap.ps1",
            "runtime/skilltree_hook.py",
            "scripts/setup.ps1",
            "skills/skill-router/SKILL.md",
        ]
    )
    hook_paths = [
        "hooks/hooks.json",
        "runtime/skilltree_bootstrap.ps1",
        "runtime/skilltree_hook.py",
    ]
    manifest: dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "plugin": {
            "name": "skilltree",
            "version": version,
            "manifest_path": ".codex-plugin/plugin.json",
            "sha256": _sha256(plugin_root / ".codex-plugin" / "plugin.json"),
        },
        "core": {
            "distribution": CORE_DISTRIBUTION,
            "version": version,
            "wheel": wheel_path.as_posix(),
            "sha256": _sha256(plugin_root / wheel_path),
        },
        "schema": {"version": SCHEMA_VERSION, "migration_version": 1},
        "migrations": [{"version": 1, "path": migration_path, "sha256": _sha256(plugin_root / migration_path)}],
        "requirements_lock": {"path": "requirements.lock", "sha256": _sha256(plugin_root / "requirements.lock")},
        "runtime_files": [{"path": path, "sha256": _sha256(plugin_root / path)} for path in runtime_paths],
        "wheels": [{"filename": wheel_filename, "distribution": CORE_DISTRIBUTION, "version": version, "sha256": _sha256(plugin_root / wheel_path)}],
        "hook_bundle": {
            "algorithm": "sha256-sorted-path-file-hashes/v1",
            "files": hook_paths,
            "hash": _hook_bundle_hash(plugin_root, hook_paths),
        },
    }
    manifest["bundle_hash"] = _bundle_hash(manifest)
    return manifest


def _validate_plugin_and_core(plugin_root: Path, manifest: dict[str, Any]) -> None:
    plugin = manifest["plugin"]
    core = manifest["core"]
    schema = manifest["schema"]
    _require_exact_keys(plugin, {"name", "version", "manifest_path", "sha256"}, "plugin")
    _require_exact_keys(core, {"distribution", "version", "wheel", "sha256"}, "core")
    _require_exact_keys(schema, {"version", "migration_version"}, "schema")
    if plugin["name"] != "skilltree" or core["distribution"] != CORE_DISTRIBUTION:
        raise BundleValidationError("unexpected plugin or core distribution")
    if plugin["version"] != core["version"] or schema != {"version": SCHEMA_VERSION, "migration_version": 1}:
        raise BundleValidationError("version contract mismatch")
    _validate_file_entry(plugin_root, plugin, "manifest_path")
    _validate_file_entry(plugin_root, core, "wheel")


def _validate_migrations(plugin_root: Path, manifest: dict[str, Any]) -> None:
    migrations = manifest["migrations"]
    if not isinstance(migrations, list) or len(migrations) != manifest["schema"]["migration_version"]:
        raise BundleValidationError("migration count mismatch")
    expected_versions = list(range(1, manifest["schema"]["migration_version"] + 1))
    found_paths: set[str] = set()
    for expected_version, migration in zip(expected_versions, migrations, strict=True):
        _require_exact_keys(migration, {"version", "path", "sha256"}, "migration")
        if migration["version"] != expected_version or not isinstance(migration["path"], str):
            raise BundleValidationError("migration sequence mismatch")
        match = _MIGRATION_PATTERN.fullmatch(migration["path"])
        if not match or int(match.group(1)) != expected_version:
            raise BundleValidationError("invalid migration path")
        found_paths.add(migration["path"])
        _validate_file_entry(plugin_root, migration, "path")
    actual_paths = {path.relative_to(plugin_root).as_posix() for path in (plugin_root / "migrations").glob("*.sql")}
    if actual_paths != found_paths:
        raise BundleValidationError("migration directory does not match manifest")


def _validate_requirements(plugin_root: Path, manifest: dict[str, Any]) -> None:
    entry = manifest["requirements_lock"]
    _require_exact_keys(entry, {"path", "sha256"}, "requirements lock")
    path = _validate_file_entry(plugin_root, entry, "path")
    expected = f"{manifest['core']['distribution']}=={manifest['core']['version']} --hash={manifest['core']['sha256']}\n"
    if path.read_text(encoding="utf-8") != expected:
        raise BundleValidationError("requirements lock is not a complete wheel hash lock")


def _validate_runtime_files(plugin_root: Path, manifest: dict[str, Any]) -> None:
    entries = manifest["runtime_files"]
    if not isinstance(entries, list):
        raise BundleValidationError("runtime_files must be an array")
    paths = []
    for entry in entries:
        _require_exact_keys(entry, {"path", "sha256"}, "runtime file")
        paths.append(entry["path"])
        _validate_file_entry(plugin_root, entry, "path")
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise BundleValidationError("runtime files must be unique and sorted")


def _validate_wheels(plugin_root: Path, manifest: dict[str, Any]) -> None:
    wheels = manifest["wheels"]
    if not isinstance(wheels, list) or len(wheels) != 1:
        raise BundleValidationError("P0 must contain exactly one core wheel")
    wheel = wheels[0]
    _require_exact_keys(wheel, {"filename", "distribution", "version", "sha256"}, "wheel")
    if wheel["distribution"] != CORE_DISTRIBUTION or wheel["version"] != manifest["core"]["version"]:
        raise BundleValidationError("wheel version mismatch")
    if not isinstance(wheel["filename"], str) or not wheel["filename"].endswith(".whl"):
        raise BundleValidationError("non-wheel runtime artifact")
    wheel_path = plugin_root / "runtime" / "wheels" / wheel["filename"]
    if not wheel_path.is_file() or _sha256(wheel_path) != wheel["sha256"]:
        raise BundleValidationError("wheel hash mismatch")
    if manifest["core"]["wheel"] != f"runtime/wheels/{wheel['filename']}" or manifest["core"]["sha256"] != wheel["sha256"]:
        raise BundleValidationError("core wheel declaration mismatch")
    disallowed = [path for path in (plugin_root / "runtime" / "wheels").iterdir() if not path.name.endswith(".whl")]
    if disallowed:
        raise BundleValidationError("sdist or unknown runtime artifact found")


def _validate_hook_bundle(plugin_root: Path, manifest: dict[str, Any]) -> None:
    hook_bundle = manifest["hook_bundle"]
    _require_exact_keys(hook_bundle, {"algorithm", "files", "hash"}, "hook bundle")
    files = hook_bundle["files"]
    if hook_bundle["algorithm"] != "sha256-sorted-path-file-hashes/v1" or not isinstance(files, list):
        raise BundleValidationError("unsupported hook bundle contract")
    if files != sorted(files) or files != ["hooks/hooks.json", "runtime/skilltree_bootstrap.ps1", "runtime/skilltree_hook.py"]:
        raise BundleValidationError("hook bundle file set mismatch")
    for path in files:
        _safe_relative_path(plugin_root, path)
    if hook_bundle["hash"] != _hook_bundle_hash(plugin_root, files):
        raise BundleValidationError("hook bundle hash mismatch")


def _validate_file_entry(plugin_root: Path, entry: dict[str, Any], path_key: str) -> Path:
    path = _safe_relative_path(plugin_root, entry[path_key])
    if not _is_sha256(entry["sha256"]) or not path.is_file() or _sha256(path) != entry["sha256"]:
        raise BundleValidationError(f"hash mismatch for {entry[path_key]}")
    return path


def _safe_relative_path(plugin_root: Path, relative_path: object) -> Path:
    if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
        raise BundleValidationError("invalid relative path")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise BundleValidationError("path escapes plugin root")
    resolved = (plugin_root / relative).resolve()
    if plugin_root not in resolved.parents and resolved != plugin_root:
        raise BundleValidationError("path escapes plugin root")
    return resolved


def _build_core_wheel(repository_root: Path, wheel_dir: Path) -> None:
    project = _read_json(repository_root / "pyproject.toml", toml=True)
    metadata = project["project"]
    distribution = metadata["name"]
    version = metadata["version"]
    normalized_name = distribution.replace("-", "_")
    wheel_path = wheel_dir / f"{normalized_name}-{version}-py3-none-any.whl"
    package_root = repository_root / "src" / "skilltree"
    if not package_root.is_dir():
        raise BundleValidationError("skilltree package source is missing")

    records: list[tuple[str, str, str]] = []
    with ZipFile(wheel_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as wheel:
        for source_path in sorted(package_root.rglob("*.py")):
            archive_path = source_path.relative_to(repository_root / "src").as_posix()
            _write_wheel_member(wheel, archive_path, source_path.read_bytes(), records)
        dist_info = f"{normalized_name}-{version}.dist-info"
        _write_wheel_member(wheel, f"{dist_info}/METADATA", _metadata_bytes(metadata), records)
        _write_wheel_member(wheel, f"{dist_info}/WHEEL", b"Wheel-Version: 1.0\nGenerator: skilltree.bundle\nRoot-Is-Purelib: true\nTag: py3-none-any\n", records)
        _write_wheel_member(wheel, f"{dist_info}/entry_points.txt", _entry_points_bytes(metadata), records)
        record_path = f"{dist_info}/RECORD"
        records.append((record_path, "", ""))
        record_bytes = "".join(",".join(_csv_quote(column) for column in record) + "\n" for record in records
        ).encode("utf-8")
        _write_zip_member(wheel, record_path, record_bytes)


def _write_wheel_member(wheel: ZipFile, archive_path: str, contents: bytes, records: list[tuple[str, str, str]]) -> None:
    _write_zip_member(wheel, archive_path, contents)
    digest = base64.urlsafe_b64encode(hashlib.sha256(contents).digest()).rstrip(b"=").decode("ascii")
    records.append((archive_path, f"sha256={digest}", str(len(contents))))


def _write_zip_member(wheel: ZipFile, archive_path: str, contents: bytes) -> None:
    member = ZipInfo(archive_path, date_time=(1980, 1, 1, 0, 0, 0))
    member.compress_type = ZIP_DEFLATED
    member.external_attr = 0o100644 << 16
    wheel.writestr(member, contents, compress_type=ZIP_DEFLATED, compresslevel=9)


def _metadata_bytes(project: dict[str, Any]) -> bytes:
    summary = project.get("description", "")
    return (
        "Metadata-Version: 2.1\n"
        f"Name: {project['name']}\n"
        f"Version: {project['version']}\n"
        f"Summary: {summary}\n"
        f"Requires-Python: {project['requires-python']}\n"
    ).encode("utf-8")


def _entry_points_bytes(project: dict[str, Any]) -> bytes:
    scripts = project.get("scripts", {})
    if not scripts:
        return b""
    lines = ["[console_scripts]"]
    lines.extend(f"{name} = {target}" for name, target in sorted(scripts.items()))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _csv_quote(value: str) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="")
    writer.writerow([value])
    return output.getvalue()


def _find_core_wheel(wheel_dir: Path, version: str) -> Path:
    expected_name = f"skilltree_core-{version}-py3-none-any.whl"
    matches = list(wheel_dir.glob(expected_name))
    if len(matches) != 1:
        raise BundleValidationError("expected pure Python core wheel was not built")
    return matches[0]


def _read_json(path: Path, *, toml: bool = False) -> dict[str, Any]:
    try:
        if toml:
            import tomllib

            return tomllib.loads(path.read_text(encoding="utf-8"))
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BundleValidationError(f"cannot read {path.name}") from error
    if not isinstance(value, dict):
        raise BundleValidationError(f"{path.name} must be an object")
    return value


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _require_exact_keys(value: object, expected: set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise BundleValidationError(f"invalid {name} schema")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _hook_bundle_hash(plugin_root: Path, files: list[str]) -> str:
    digest = hashlib.sha256()
    for relative_path in sorted(files):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_sha256(_safe_relative_path(plugin_root, relative_path)).encode("ascii"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def _bundle_hash(manifest: dict[str, Any]) -> str:
    unsigned = dict(manifest)
    unsigned.pop("bundle_hash", None)
    canonical = json.dumps(unsigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()
