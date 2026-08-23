"""Standalone P0 Bundle integrity checker used before the runtime venv exists."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main(plugin_root: Path) -> int:
    try:
        manifest = json.loads((plugin_root / "runtime" / "bundle-manifest.json").read_text(encoding="utf-8"))
        unsigned = dict(manifest)
        bundle_hash = unsigned.pop("bundle_hash")
        canonical = json.dumps(unsigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if bundle_hash != "sha256:" + hashlib.sha256(canonical).hexdigest():
            return 3
        entries = [manifest["plugin"], manifest["core"], manifest["requirements_lock"], *manifest["migrations"], *manifest["runtime_files"]]
        for entry in entries:
            relative = entry.get("path") or entry.get("wheel") or entry.get("manifest_path")
            path = (plugin_root / relative).resolve()
            if plugin_root not in path.parents or not path.is_file() or entry["sha256"] != sha256(path):
                return 3
        wheels = manifest["wheels"]
        if not isinstance(wheels, list) or len(wheels) != 1:
            return 3
        wheel_names = []
        for wheel in wheels:
            if set(wheel) != {"filename", "distribution", "version", "sha256"}:
                return 3
            wheel_path = (plugin_root / "runtime" / "wheels" / wheel["filename"]).resolve()
            if plugin_root not in wheel_path.parents or not wheel_path.is_file() or wheel["sha256"] != sha256(wheel_path):
                return 3
            wheel_names.append(wheel["filename"])
        actual_wheels = sorted(path.name for path in (plugin_root / "runtime" / "wheels").iterdir() if path.is_file())
        if sorted(wheel_names) != actual_wheels:
            return 3
        hook_bundle = manifest["hook_bundle"]
        if hook_bundle["algorithm"] != "sha256-sorted-path-file-hashes/v1":
            return 3
        hook_files = hook_bundle["files"]
        if hook_files != sorted(hook_files) or hook_files != [
            "hooks/hooks.json", "runtime/skilltree_bootstrap.ps1", "runtime/skilltree_hook.py",
            "scripts/invoke-hook.ps1"
        ]:
            return 3
        digest = hashlib.sha256()
        for relative in hook_files:
            path = (plugin_root / relative).resolve()
            if plugin_root not in path.parents or not path.is_file():
                return 3
            digest.update(relative.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(sha256(path).encode("ascii"))
            digest.update(b"\n")
        if hook_bundle["hash"] != "sha256:" + digest.hexdigest():
            return 3
        if [item["version"] for item in manifest["migrations"]] != list(range(1, manifest["schema"]["migration_version"] + 1)):
            return 3
    except (KeyError, OSError, TypeError, ValueError):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
