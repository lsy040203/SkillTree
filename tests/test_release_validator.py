from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from tools.validate_plugin import validate_release_bundle


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PLUGIN = ROOT / "plugins" / "skilltree"


def _copy_plugin(tmp_path: Path) -> Path:
    destination = tmp_path / "skilltree"
    shutil.copytree(SOURCE_PLUGIN, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return destination


def test_current_plugin_passes_release_validator(tmp_path: Path) -> None:
    report = validate_release_bundle(_copy_plugin(tmp_path))
    assert report["ok"] is True, report
    assert report["schema_version"] == "skilltree-release-validation/v1"
    assert report["files"] == sorted(report["files"])


def test_forbidden_and_unlisted_artifacts_are_rejected(tmp_path: Path) -> None:
    plugin = _copy_plugin(tmp_path)
    (plugin / "runtime" / "unexpected.py").write_text("print('x')\n", encoding="utf-8")
    (plugin / "replay.oci.tar").write_bytes(b"fixture")
    report = validate_release_bundle(plugin)
    codes = {(error["code"], error["path"]) for error in report["errors"]}
    assert ("unlisted_file", "runtime/unexpected.py") in codes
    assert ("forbidden_artifact", "replay.oci.tar") in codes


def test_sensitive_content_is_rejected_without_echoing_secret(tmp_path: Path) -> None:
    plugin = _copy_plugin(tmp_path)
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    path = plugin / "runtime" / "secret.txt"
    path.write_text(f"api_key={secret}\n", encoding="utf-8")
    report = validate_release_bundle(plugin)
    encoded = json.dumps(report)
    assert any(error["code"] == "credential_content" for error in report["errors"])
    assert secret not in encoded


def test_cli_writes_stable_json_and_nonzero_for_invalid_bundle(tmp_path: Path) -> None:
    plugin = _copy_plugin(tmp_path)
    (plugin / "runtime" / "extra.py").write_text("x\n", encoding="utf-8")
    output = tmp_path / "report.json"
    completed = subprocess.run(
        [sys.executable, "tools/validate_plugin.py", "--plugin-root", str(plugin), "--json-out", str(output)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["schema_version"] == "skilltree-release-validation/v1"
    assert "extra.py" in json.dumps(report)


def test_build_bundle_can_stage_a_new_output(tmp_path: Path) -> None:
    destination = tmp_path / "staged" / "skilltree"
    report_path = tmp_path / "build.json"
    completed = subprocess.run(
        [sys.executable, "tools/build_bundle.py", "--output-dir", str(destination), "--json-out", str(report_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert destination.is_dir()
    assert json.loads(report_path.read_text(encoding="utf-8"))["ok"] is True
