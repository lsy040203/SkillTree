from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools.generate_sbom import SbomValidationError, generate_sbom


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PLUGIN = ROOT / "plugins" / "skilltree"


def _copy_plugin(tmp_path: Path) -> Path:
    destination = tmp_path / "skilltree"
    shutil.copytree(SOURCE_PLUGIN, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return destination


def test_sbom_is_cyclonedx_1_5_and_deterministic(tmp_path: Path) -> None:
    plugin = _copy_plugin(tmp_path)
    first = generate_sbom(plugin)
    second = generate_sbom(plugin)
    assert json.dumps(first, sort_keys=True, indent=2) == json.dumps(second, sort_keys=True, indent=2)
    assert first["bomFormat"] == "CycloneDX"
    assert first["specVersion"] == "1.5"
    assert first["components"] == sorted(first["components"], key=lambda item: (item["name"].lower(), item["version"]))
    assert first["components"][0]["name"] == "skilltree-core"
    assert len(first["components"][0]["hashes"][0]["content"]) == 64


def test_lock_hash_must_match_local_manifest(tmp_path: Path) -> None:
    plugin = _copy_plugin(tmp_path)
    lock = plugin / "requirements.lock"
    lock.write_text(lock.read_text(encoding="utf-8").replace("a298bff0", "b298bff0"), encoding="utf-8")
    with pytest.raises(SbomValidationError, match="invalid Plugin Bundle"):
        generate_sbom(plugin)


def test_missing_lock_entry_is_rejected(tmp_path: Path) -> None:
    plugin = _copy_plugin(tmp_path)
    (plugin / "requirements.lock").write_text("other-package==1.0 --hash=sha256:" + "0" * 64 + "\n", encoding="utf-8")
    with pytest.raises(SbomValidationError, match="invalid Plugin Bundle"):
        generate_sbom(plugin)
