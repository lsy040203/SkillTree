from __future__ import annotations

import json
from pathlib import Path

from tools.check_compatibility import validate_matrix


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "implementation" / "p7" / "COMPATIBILITY.md"


def test_repository_compatibility_matrix_is_valid() -> None:
    report = validate_matrix(MATRIX)
    assert report["ok"] is True, report
    assert report["entries"] >= 6
    assert report["schema_version"] == "skilltree-compatibility-check/v1"


def test_supported_entry_without_evidence_is_rejected(tmp_path: Path) -> None:
    source = MATRIX.read_text(encoding="utf-8")
    broken = source.replace('"evidence": "pyproject.toml"', '"evidence": ""', 1)
    path = tmp_path / "COMPATIBILITY.md"
    path.write_text(broken, encoding="utf-8")
    report = validate_matrix(path)
    assert report["ok"] is False
    assert any(error["code"] == "missing_evidence" for error in report["errors"])


def test_duplicate_entry_is_rejected(tmp_path: Path) -> None:
    source = MATRIX.read_text(encoding="utf-8")
    marker = '"value": "Windows PowerShell"'
    start = source.index("    {", source.index(marker) - 40)
    end = source.index("    },", start) + len("    },")
    entry = source[start:end]
    path = tmp_path / "COMPATIBILITY.md"
    path.write_text(source[:end] + "\n" + entry + source[end:], encoding="utf-8")
    report = validate_matrix(path)
    assert report["ok"] is False
    assert any(error["code"] == "duplicate_entry" for error in report["errors"])


def test_cli_report_is_json(tmp_path: Path) -> None:
    output = tmp_path / "compat.json"
    from tools.check_compatibility import main

    assert main(["--matrix", str(MATRIX), "--json-out", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["ok"] is True
