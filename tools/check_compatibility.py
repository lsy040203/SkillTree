"""Validate the machine-readable compatibility matrix in Markdown."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "skilltree-compatibility-check/v1"
_BLOCK = re.compile(r"```skilltree-compatibility/v1\s*\n(?P<body>\{.*?\})\s*\n```", re.DOTALL)
_SURFACES = {"os", "python", "codex", "hook", "tool", "replay"}
_STATUSES = {"supported", "unsupported"}


class CompatibilityValidationError(ValueError):
    """Raised when a compatibility matrix is malformed or unverifiable."""


def read_matrix(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = _BLOCK.search(text)
    if not match:
        raise CompatibilityValidationError("compatibility block missing")
    try:
        matrix = json.loads(match["body"])
    except json.JSONDecodeError as exc:
        raise CompatibilityValidationError("compatibility block is invalid JSON") from exc
    if not isinstance(matrix, dict) or matrix.get("schema_version") != "skilltree-compatibility/v1":
        raise CompatibilityValidationError("unsupported compatibility schema")
    entries = matrix.get("entries")
    if not isinstance(entries, list) or not entries:
        raise CompatibilityValidationError("compatibility entries are required")
    return matrix


def validate_matrix(path: Path) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    try:
        matrix = read_matrix(path)
        entries = matrix["entries"]
        seen: set[tuple[str, str]] = set()
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) != {"surface", "value", "status", "evidence"}:
                errors.append(_error("entry_schema", str(index), "entry must contain exactly surface, value, status, evidence"))
                continue
            surface, value, status, evidence = (entry["surface"], entry["value"], entry["status"], entry["evidence"])
            key = (str(surface), str(value))
            if key in seen:
                errors.append(_error("duplicate_entry", f"{surface}:{value}", "duplicate compatibility entry"))
            seen.add(key)
            if surface not in _SURFACES:
                errors.append(_error("unknown_surface", str(surface), "unsupported compatibility surface"))
            if status not in _STATUSES:
                errors.append(_error("invalid_status", str(status), "status must be supported or unsupported"))
            if not isinstance(value, str) or not value.strip():
                errors.append(_error("empty_value", str(surface), "compatibility value is required"))
            if status == "supported" and (not isinstance(evidence, str) or not evidence.strip()):
                errors.append(_error("missing_evidence", f"{surface}:{value}", "supported entries require evidence"))
    except (OSError, CompatibilityValidationError) as exc:
        errors.append(_error("matrix_invalid", path.name, str(exc)))
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not errors,
        "entries": len(matrix.get("entries", [])) if "matrix" in locals() and isinstance(matrix, dict) else 0,
        "errors": sorted(errors, key=lambda item: (item["path"], item["code"])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = validate_matrix(args.matrix)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return 0 if report["ok"] else 1


def _error(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path.replace("\\", "/"), "message": message[:240]}


if __name__ == "__main__":
    raise SystemExit(main())
