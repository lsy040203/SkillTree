"""Explicit, single-file Markdown cold-start import for P5."""

from __future__ import annotations

import re
from pathlib import Path

from skilltree.core.memory_candidates import MemoryCandidateSchemaError
from skilltree.core.memory_store import MemoryStoreError, create_import_memory_candidate
from skilltree.core.storage import Database


_MAX_BYTES = 64 * 1024
_FRONTMATTER_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class MemoryImportError(ValueError):
    """A bounded public error for an explicitly selected import file."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def import_markdown_candidates(
    database: Database, *, source: Path, user_id: str, workspace_id: str
) -> dict[str, object]:
    """Convert one selected Markdown file to exactly one pending candidate."""
    if not source.is_absolute() or not source.is_file():
        raise MemoryImportError("invalid_schema")
    try:
        if source.stat().st_size > _MAX_BYTES:
            raise MemoryImportError("invalid_schema")
        document = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise MemoryImportError("invalid_schema") from error
    metadata, body = _split_frontmatter(document)
    candidate = _candidate_from_markdown(metadata, body)
    try:
        created = create_import_memory_candidate(
            database, user_id=user_id, workspace_id=workspace_id, candidate=candidate
        )
    except MemoryCandidateSchemaError as error:
        raise MemoryImportError("invalid_schema") from error
    except MemoryStoreError as error:
        raise MemoryImportError(error.code) from error
    return {"created": 1, "pending": 1, "candidate_ids": [created["candidate_id"]]}


def _split_frontmatter(document: str) -> tuple[dict[str, str], str]:
    lines = document.splitlines()
    if not lines or lines[0] != "---":
        raise MemoryImportError("invalid_schema")
    metadata: dict[str, str] = {}
    for index in range(1, len(lines)):
        line = lines[index]
        if line == "---":
            body = "\n".join(lines[index + 1:]).strip()
            if not body:
                raise MemoryImportError("invalid_schema")
            return metadata, body
        if ":" not in line:
            raise MemoryImportError("invalid_schema")
        key, value = (part.strip() for part in line.split(":", 1))
        if not _FRONTMATTER_KEY.fullmatch(key) or not value or key in metadata:
            raise MemoryImportError("invalid_schema")
        metadata[key] = value
    raise MemoryImportError("invalid_schema")


def _candidate_from_markdown(metadata: dict[str, str], body: str) -> dict[str, object]:
    kind = metadata.get("kind")
    if kind == "profile":
        namespace = metadata.get("namespace", "preference")
        key = metadata.get("key")
        if key is None:
            raise MemoryImportError("invalid_schema")
        return {
            "schema_version": "skilltree/v1",
            "profile_fields": [{
                "namespace": namespace,
                "key": key,
                "value": body,
                "confidence": 0.5,
                "reason": "explicit Markdown import",
                "source_kind": "markdown_import",
                "evidence_event_ids": [],
            }],
            "procedural_candidates": [],
        }
    if kind == "procedure":
        applies_to = metadata.get("applies_to")
        if applies_to is None:
            raise MemoryImportError("invalid_schema")
        scenario_key = metadata.get("scenario_key", "")
        scenario_label = metadata.get("scenario_label", "")
        return {
            "schema_version": "skilltree/v1",
            "profile_fields": [],
            "procedural_candidates": [{
                "task_type": metadata.get("task_type", applies_to),
                "scenario_key": scenario_key,
                "scenario_label": scenario_label,
                "rule": body,
                "why": "explicit Markdown import",
                "applies_to": applies_to,
                "strength": "weak",
                "when": metadata.get("when", ""),
                "recommended_skill_names": [],
                "ordering_constraints": [],
                "avoid_when": metadata.get("avoid_when", ""),
                "evidence_event_ids": [],
                "outcome_evidence_kind": "none",
            }],
        }
    raise MemoryImportError("invalid_schema")
