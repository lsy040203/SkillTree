"""Safe, read-only P1 discovery and scanning of a confirmed Skill root."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from skilltree.core.sanitize import sanitize_description


REGISTRY_CAPACITY = 500
_SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class RegistryError(ValueError):
    """Raised for a registry domain error with a public error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ScannedSkill:
    name: str
    description: str
    path: Path
    content_hash: str
    state: str
    diagnostic: str | None


def discover_setup_candidates(
    provided_root: str | None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    forbidden_roots: Iterable[Path] = (),
) -> list[Path]:
    """Return existing approved-location candidates without reading their contents."""
    environment = os.environ if environ is None else environ
    current_home = Path.home() if home is None else home
    raw_candidates: list[Path] = []
    if provided_root is not None:
        raw_candidates.append(Path(provided_root))
    if environment.get("CODEX_HOME"):
        raw_candidates.append(Path(environment["CODEX_HOME"]) / "skills")
    raw_candidates.append(current_home / ".codex" / "skills")

    candidates: list[Path] = []
    for candidate in raw_candidates:
        if not candidate.exists():
            continue
        normalized = validate_skill_root(candidate, forbidden_roots=forbidden_roots)
        if normalized not in candidates:
            candidates.append(normalized)
    return candidates


def scan_skill_root(skill_root: Path) -> list[ScannedSkill]:
    """Count safe files before reading frontmatter, then return immutable scan records."""
    root = validate_skill_root(skill_root)
    skill_files = _find_skill_files(root)
    if len(skill_files) > REGISTRY_CAPACITY:
        raise RegistryError("registry_capacity_exceeded")

    records: list[ScannedSkill] = []
    claimed_names: set[str] = set()
    for skill_file in skill_files:
        record = _scan_file(skill_file)
        if record.name in claimed_names:
            record = _trusted_invalid_record(skill_file, record.content_hash, "duplicate_name")
        claimed_names.add(record.name)
        records.append(record)
    return records


def validate_skill_root(path: Path, *, forbidden_roots: Iterable[Path] = ()) -> Path:
    """Normalize an existing local directory and reject unsafe root syntaxes."""
    raw = str(path)
    if not path.is_absolute() or raw.startswith("\\\\") or any(character in raw for character in "*?"):
        raise RegistryError("out_of_scope")
    try:
        normalized = path.resolve(strict=True)
    except OSError:
        raise RegistryError("out_of_scope") from None
    if not normalized.is_dir():
        raise RegistryError("out_of_scope")
    for forbidden in forbidden_roots:
        try:
            forbidden_path = forbidden.resolve(strict=False)
        except OSError:
            raise RegistryError("out_of_scope") from None
        if _is_within(normalized, forbidden_path) or _is_within(forbidden_path, normalized):
            raise RegistryError("out_of_scope")
    return normalized


def _find_skill_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for candidate in root.rglob("SKILL.md"):
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            raise RegistryError("out_of_scope") from None
        if not _is_within(resolved, root):
            raise RegistryError("out_of_scope")
        if ".system" in resolved.relative_to(root).parts:
            continue
        if resolved not in files:
            files.append(resolved)
    return sorted(files, key=lambda item: item.as_posix())


def _scan_file(path: Path) -> ScannedSkill:
    try:
        contents = path.read_bytes()
        text = contents.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return _invalid_record(path, _path_hash(path), "frontmatter_invalid")
    content_hash = "sha256:" + hashlib.sha256(contents).hexdigest()
    metadata, diagnostic = _parse_frontmatter(text)
    invalid = diagnostic is not None
    raw_name = metadata.get("name", "")
    if _SKILL_NAME.fullmatch(raw_name) is None:
        name = _invalid_name(content_hash)
        invalid = True
        diagnostic = diagnostic or "invalid_name"
    else:
        name = raw_name
    raw_description = metadata.get("description", "")
    sanitized = sanitize_description(raw_description)
    encoded_description = sanitized.value.encode("utf-8")
    if sanitized.state == "rejected" or not sanitized.value.strip():
        description = f"User-managed Skill: {name}"
        diagnostic = diagnostic or sanitized.reason or "description_rewritten"
    elif len(encoded_description) > 500:
        description = _truncate_utf8(sanitized.value, 500)
        diagnostic = diagnostic or "description_truncated"
    else:
        description = sanitized.value
    return ScannedSkill(name, description, path, content_hash, "trusted", diagnostic)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str | None]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}, "frontmatter_invalid"
    try:
        closing_index = lines.index("---", 1)
    except ValueError:
        return {}, "frontmatter_invalid"
    metadata: dict[str, str] = {}
    for line in lines[1:closing_index]:
        key, separator, value = line.partition(":")
        if not separator:
            return {}, "frontmatter_invalid"
        key = key.strip()
        if key in {"name", "description"} and key not in metadata:
            metadata[key] = _unquote_frontmatter_value(value.strip())
    return metadata, None


def _invalid_record(path: Path, content_hash: str, diagnostic: str) -> ScannedSkill:
    return _trusted_invalid_record(path, content_hash, diagnostic)


def _trusted_invalid_record(path: Path, content_hash: str, diagnostic: str) -> ScannedSkill:
    name = _invalid_name(content_hash)
    return ScannedSkill(name, f"User-managed Skill: {name}", path, content_hash, "trusted", diagnostic)


def _truncate_utf8(value: str, limit: int) -> str:
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore").strip() or "User-managed Skill"


def _unquote_frontmatter_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def _invalid_name(content_hash: str) -> str:
    digest = content_hash.removeprefix("sha256:")
    return "invalid-" + digest[:12]


def _path_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.as_posix().encode("utf-8")).hexdigest()


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True
