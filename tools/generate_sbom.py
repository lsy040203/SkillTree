"""Generate a deterministic offline CycloneDX SBOM for a Plugin Bundle."""

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


SBOM_SCHEMA_VERSION = "http://cyclonedx.org/schema/bom-1.5.schema.json"
_LOCK_LINE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[A-Za-z0-9_.+!-]+)(?P<hashes>(?:\s+--hash=sha256:[0-9a-fA-F]{64})+)\s*$")


class SbomValidationError(ValueError):
    """Raised when the lock and local Bundle do not describe the same files."""


def generate_sbom(plugin_root: Path) -> dict[str, Any]:
    """Build a stable CycloneDX object from local lock and manifest data."""
    plugin_root = plugin_root.resolve()
    try:
        manifest = validate_bundle(plugin_root)
    except (BundleValidationError, OSError, ValueError) as exc:
        raise SbomValidationError("invalid Plugin Bundle") from exc

    lock_entries = _read_lock(plugin_root / manifest["requirements_lock"]["path"])
    manifest_entries = _manifest_entries(manifest)
    components: list[dict[str, Any]] = []
    for name, version, hashes in lock_entries:
        key = (_normalize_name(name), version)
        expected = manifest_entries.get(key)
        if expected is None:
            raise SbomValidationError(f"lock entry missing from manifest: {name}=={version}")
        if expected not in hashes:
            raise SbomValidationError(f"lock hash mismatch for {name}=={version}")
        components.append(
            {
                "bom-ref": f"pkg:pypi/{_normalize_name(name)}@{version}",
                "hashes": [{"alg": "SHA-256", "content": expected}],
                "name": name,
                "purl": f"pkg:pypi/{_normalize_name(name)}@{version}",
                "type": "library",
                "version": version,
            }
        )

    components.sort(key=lambda item: (item["name"].lower(), item["version"]))
    dependencies = [
        {"dependsOn": [], "ref": component["bom-ref"]}
        for component in components
    ]
    dependencies.sort(key=lambda item: item["ref"])
    return {
        "bomFormat": "CycloneDX",
        "components": components,
        "dependencies": dependencies,
        "metadata": {"component": {"name": "skilltree", "type": "application"}},
        "schema": SBOM_SCHEMA_VERSION,
        "specVersion": "1.5",
        "version": 1,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        sbom = generate_sbom(args.plugin_root)
    except SbomValidationError as exc:
        print(f"sbom generation failed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(sbom, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return 0


def _read_lock(path: Path) -> list[tuple[str, str, set[str]]]:
    entries: list[tuple[str, str, set[str]]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LOCK_LINE.fullmatch(line)
        if not match:
            raise SbomValidationError(f"invalid lock entry at line {line_number}")
        hashes = {value.split(":", 1)[1].lower() for value in re.findall(r"--hash=(sha256:[0-9a-fA-F]{64})", match["hashes"])}
        entries.append((match["name"], match["version"], hashes))
    if not entries:
        raise SbomValidationError("requirements lock is empty")
    return entries


def _manifest_entries(manifest: dict[str, Any]) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for wheel in manifest.get("wheels", []):
        name = wheel.get("distribution")
        version = wheel.get("version")
        digest = wheel.get("sha256", "")
        if not isinstance(name, str) or not isinstance(version, str) or not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise SbomValidationError("invalid wheel manifest entry")
        result[(_normalize_name(name), version)] = digest.split(":", 1)[1].lower()
    return result


def _normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


if __name__ == "__main__":
    raise SystemExit(main())
