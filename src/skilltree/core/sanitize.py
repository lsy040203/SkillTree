"""Release-packaged sanitize/v1 rules used at persistence boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SanitizedText:
    value: str
    state: str
    reason: str | None = None


_REJECTED_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_-]?key|token|password)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}"),
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
)
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


def sanitize_description(value: str) -> SanitizedText:
    """Apply the fixed sanitize/v1 subset required by P1 descriptions."""
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return SanitizedText("", "rejected", "description_rejected")
    if any(pattern.search(value) for pattern in _REJECTED_PATTERNS):
        return SanitizedText("", "rejected", "description_rejected")
    redacted = _EMAIL.sub("[REDACTED:email]", value)
    return SanitizedText(redacted, "redacted" if redacted != value else "clean")


def contains_secret_pattern(value: str) -> bool:
    """Detect credential-like material without rejecting normal source text."""
    return any(pattern.search(value) for pattern in _REJECTED_PATTERNS)
