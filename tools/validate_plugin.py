"""Validate a staged SkillTree release Plugin without installing it."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from skilltree.core.bundle import BundleValidationError, validate_bundle


SCHEMA_VERSION = "skilltree-release-validation/v1"
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[^\s]{12,}"),
)
_FORBIDDEN_NAMES = {
    ".env",
    ".env.local",
    "outbox",
    "replay.blob",
    "replay.sqlite3",
}
_FORBIDDEN_SUFFIXES = (".sqlite", ".sqlite3", ".db", ".oci.tar", ".pem", ".key", ".pyc")
_ALLOWED_ROOT_FILES = {"README.md", "requirements.lock"}
_ALLOWED_ROOT_DIRS = {".codex-plugin", "hooks", "migrations", "runtime", "scripts", "skills"}


def validate_release_bundle(plugin_root: Path) -> dict[str, Any]:
    """Return a stable, redacted validation report for a staged Plugin."""
    plugin_root = plugin_root.resolve()
    errors: list[dict[str, str]] = []
    if not plugin_root.is_dir():
        errors.append(_error("plugin_root_missing", "<plugin-root>", "plugin root is missing"))
        return _report(plugin_root, errors)

    try:
        manifest = validate_bundle(plugin_root)
    except (BundleValidationError, OSError, ValueError) as exc:
        errors.append(_error("core_bundle_invalid", "runtime/bundle-manifest.json", _safe_message(str(exc))))
        manifest = None

    files = _relative_files(plugin_root)
    declared = _declared_files(manifest)
    for relative in files:
        parts = Path(relative).parts
        if parts and (parts[0] not in _ALLOWED_ROOT_DIRS and relative not in _ALLOWED_ROOT_FILES):
            errors.append(_error("unexpected_file", relative, "file is outside the release allowlist"))
        if _is_forbidden(relative):
            errors.append(_error("forbidden_artifact", relative, "development or sensitive artifact is not releasable"))
        if declared and _is_declared_area(relative) and relative not in declared:
            errors.append(_error("unlisted_file", relative, "file is not covered by the Bundle manifest"))
        if _contains_secret(plugin_root / relative):
            errors.append(_error("credential_content", relative, "credential-like content is not releasable"))

    return _report(plugin_root, _deduplicate(errors), files=files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    report = validate_release_bundle(args.plugin_root)
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(encoded, encoding="utf-8", newline="\n")
    else:
        print(encoded, end="")
    return 0 if report["ok"] else 1


def _report(plugin_root: Path, errors: list[dict[str, str]], *, files: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not errors,
        "files": files or [],
        "errors": errors,
    }


def _error(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path.replace("\\", "/"), "message": message[:240]}


def _safe_message(message: str) -> str:
    return re.sub(r"[A-Za-z]:\\[^ ]+|/(?:[^ ]+/)+[^ ]+", "validation failed", message)[:240]


def _relative_files(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def _declared_files(manifest: dict[str, Any] | None) -> set[str]:
    if not manifest:
        return set()
    declared = {".codex-plugin/plugin.json", "requirements.lock", "hooks/hooks.json", "scripts/invoke-hook.ps1"}
    for entry in manifest.get("migrations", []):
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            declared.add(entry["path"])
    for entry in manifest.get("runtime_files", []):
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            declared.add(entry["path"])
    for entry in manifest.get("wheels", []):
        if isinstance(entry, dict) and isinstance(entry.get("filename"), str):
            declared.add(f"runtime/wheels/{entry['filename']}")
    declared.add("runtime/bundle-manifest.json")
    return declared


def _is_declared_area(relative: str) -> bool:
    return relative.startswith((".codex-plugin/", "hooks/", "migrations/", "runtime/", "scripts/", "skills/"))


def _is_forbidden(relative: str) -> bool:
    path = Path(relative)
    lowered = relative.lower()
    return (
        path.name.lower() in _FORBIDDEN_NAMES
        or path.name.lower().endswith(_FORBIDDEN_SUFFIXES)
        or ".venv" in path.parts
        or "__pycache__" in path.parts
        or lowered.endswith((".replay", ".replay.json"))
        or lowered.endswith(".tar") and "oci" in lowered
    )


def _contains_secret(path: Path) -> bool:
    if path.stat().st_size > 2 * 1024 * 1024 or path.suffix.lower() in {".whl", ".pyc"}:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def _deduplicate(errors: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted({(item["code"], item["path"]): item for item in errors}.values(), key=lambda item: (item["path"], item["code"]))


if __name__ == "__main__":
    raise SystemExit(main())
