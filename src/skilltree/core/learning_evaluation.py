"""Deterministic, fixture-only P4 learning evaluation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def evaluate_fixtures(generation: Iterable[dict[str, Any]], validation: Iterable[dict[str, Any]], *, top_k: int = 3) -> dict[str, object]:
    """Evaluate fixed sanitized records without executing Skills or reading rollouts."""
    generation_rows = _rows(generation)
    validation_rows = _rows(validation)
    generation_ids = {row["id"] for row in generation_rows}
    validation_ids = {row["id"] for row in validation_rows}
    if generation_ids & validation_ids:
        raise ValueError("fixture_overlap")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 10:
        raise ValueError("invalid_top_k")
    before = _coverage(validation_rows, {}, top_k)
    scores: dict[str, int] = defaultdict(int)
    invalid_updates = 0
    strict_count = 0
    relaxed_count = 0
    for row in generation_rows:
        for update in row.get("updates", []):
            if not _valid_update(update):
                invalid_updates += 1
                continue
            skill = update["skill"]
            scores[skill] = max(-10, min(10, scores[skill] + update["delta"]))
            if update["quality"] == "strict":
                strict_count += 1
            elif update["quality"] == "relaxed":
                relaxed_count += 1
    after = _coverage(validation_rows, scores, top_k)
    return {
        "schema_version": "skilltree/v1",
        "generation_count": len(generation_rows),
        "validation_count": len(validation_rows),
        "top_k": top_k,
        "coverage_before": before,
        "coverage_after": after,
        "invalid_update_count": invalid_updates,
        "strict_update_count": strict_count,
        "relaxed_update_count": relaxed_count,
    }


def _coverage(rows: list[dict[str, Any]], scores: dict[str, int], top_k: int) -> dict[str, object]:
    covered = 0
    for row in rows:
        ranked = row["candidates"] if not scores else sorted(row["candidates"], key=lambda name: (-scores.get(name, 0), name))
        if row["expected_skill"] in ranked[:top_k]:
            covered += 1
    total = len(rows)
    return {"covered": covered, "total": total, "rate": covered / total if total else 0.0}


def _rows(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = list(values)
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("candidates"), list) or not isinstance(row.get("expected_skill"), str):
            raise ValueError("invalid_fixture")
        if not row["id"] or not row["candidates"] or not all(isinstance(name, str) for name in row["candidates"]):
            raise ValueError("invalid_fixture")
        if "updates" in row and (not isinstance(row["updates"], list) or not all(isinstance(item, dict) for item in row["updates"])):
            raise ValueError("invalid_fixture")
    return rows


def _valid_update(update: dict[str, Any]) -> bool:
    return (
        isinstance(update.get("skill"), str)
        and isinstance(update.get("delta"), int)
        and not isinstance(update.get("delta"), bool)
        and abs(update["delta"]) <= 2
        and update.get("quality") in {"strict", "relaxed"}
    )
