"""Deterministic metadata catalog projection for semantic route selection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping


ROUTE_TOP_K = 8
ROUTE_CATALOG_DESCRIPTION_BYTES = 256
ROUTE_CATALOG_MAX_BYTES = 49_152


@dataclass(frozen=True)
class RouteCatalog:
    candidates: list[dict[str, str]]
    degraded: bool


def _truncate_utf8(value: str, limit: int) -> str:
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore").strip()


def build_metadata_catalog(rows: list[Mapping[str, str]]) -> RouteCatalog:
    """Project visible registry rows into a stable, bounded model catalog."""
    candidates = [
        {
            "name": str(row["name"]),
            "description": _truncate_utf8(
                str(row["description"]), ROUTE_CATALOG_DESCRIPTION_BYTES
            ),
            "content_hash": str(row["content_hash"]),
        }
        for row in sorted(rows, key=lambda item: str(item["name"]))
    ]
    payload = json.dumps(
        candidates,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(payload.encode("utf-8")) > ROUTE_CATALOG_MAX_BYTES:
        return RouteCatalog(candidates=[], degraded=True)
    return RouteCatalog(candidates=candidates, degraded=False)


def lexical_top_k(rows: list[Mapping[str, str]], prompt: str) -> list[dict[str, str]]:
    """Return the stable legacy fallback when the semantic catalog is too large."""
    terms = {item for item in prompt.casefold().replace("-", " ").split() if item}

    def score(row: Mapping[str, str]) -> tuple[int, str]:
        haystack = (str(row["name"]) + " " + str(row["description"])).casefold()
        return (-sum(term in haystack for term in terms), str(row["name"]))

    return [
        {key: str(row[key]) for key in ("name", "description", "content_hash")}
        for row in sorted(rows, key=score)[:ROUTE_TOP_K]
    ]
