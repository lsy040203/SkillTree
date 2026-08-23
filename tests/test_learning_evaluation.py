from __future__ import annotations

import json
from pathlib import Path

import pytest

from skilltree.core.learning_evaluation import evaluate_fixtures


ROOT = Path(__file__).resolve().parents[1]


def _fixture(name: str) -> list[dict[str, object]]:
    return json.loads((ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8"))


def test_generation_and_validation_are_separate_and_metrics_are_bounded() -> None:
    report = evaluate_fixtures(_fixture("p4_learning_generation.json"), _fixture("p4_learning_validation.json"), top_k=1)
    assert report["schema_version"] == "skilltree/v1"
    assert report["generation_count"] == 3
    assert report["validation_count"] == 3
    assert report["invalid_update_count"] == 1
    assert report["strict_update_count"] == 2
    assert report["relaxed_update_count"] == 1
    assert report["coverage_before"]["rate"] == 1.0
    assert report["coverage_after"]["rate"] == 1 / 3


def test_ties_have_stable_name_order_and_overlapping_ids_are_rejected() -> None:
    rows = [{"id": "a", "candidates": ["z", "a"], "expected_skill": "a"}]
    report = evaluate_fixtures([], rows, top_k=1)
    assert report["coverage_after"]["covered"] == 0
    with pytest.raises(ValueError, match="fixture_overlap"):
        evaluate_fixtures(rows, rows)
