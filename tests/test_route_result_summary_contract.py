from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "plugins" / "skilltree" / "skills" / "skill-router" / "SKILL.md"
SUMMARY_FIELDS = {"selected_skill", "ordered_skills", "confidence", "degraded"}
HTML_MARKER = "skilltree-route-decision:"


def validate_summary(payload: object, candidates: set[str]) -> None:
    assert isinstance(payload, dict)
    assert set(payload) == SUMMARY_FIELDS
    assert isinstance(payload["selected_skill"], str)
    assert isinstance(payload["ordered_skills"], list)
    assert payload["ordered_skills"]
    assert len(payload["ordered_skills"]) == len(set(payload["ordered_skills"]))
    assert payload["ordered_skills"][0] == payload["selected_skill"]
    assert set(payload["ordered_skills"]) <= candidates
    assert isinstance(payload["confidence"], (int, float))
    assert 0 <= payload["confidence"] <= 1
    assert isinstance(payload["degraded"], bool)


def test_skill_documents_the_four_field_route_result_summary_before_the_html_receipt() -> None:
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "Route Result JSON Summary" in skill
    for field in SUMMARY_FIELDS:
        assert f'"{field}"' in skill
    assert "metadata catalog" in skill
    assert "semantic relationship" in skill
    assert "candidate's `name` and `description`" in skill
    assert "envelope's boolean `degraded`" in skill
    assert skill.index("Route Result JSON Summary") < skill.index(HTML_MARKER)


def test_valid_summary_uses_only_offered_candidates() -> None:
    payload = {
        "selected_skill": "analyze",
        "ordered_skills": ["analyze", "lsp"],
        "confidence": 0.92,
        "degraded": False,
    }

    validate_summary(payload, {"analyze", "lsp"})


@pytest.mark.parametrize(
    ("payload", "candidates"),
    [
        ({"selected_skill": "analyze", "ordered_skills": [], "confidence": 0.9, "degraded": False}, {"analyze"}),
        ({"selected_skill": "analyze", "ordered_skills": ["lsp", "analyze"], "confidence": 0.9, "degraded": False}, {"analyze", "lsp"}),
        ({"selected_skill": "unknown", "ordered_skills": ["unknown"], "confidence": 0.9, "degraded": False}, {"analyze"}),
        ({"selected_skill": "analyze", "ordered_skills": ["analyze"], "confidence": 1.1, "degraded": False}, {"analyze"}),
        ({"selected_skill": "analyze", "ordered_skills": ["analyze"], "confidence": 0.9, "degraded": "false"}, {"analyze"}),
        ({"selected_skill": "analyze", "ordered_skills": ["analyze", "analyze"], "confidence": 0.9, "degraded": False}, {"analyze"}),
    ],
)
def test_invalid_summary_is_rejected(payload: object, candidates: set[str]) -> None:
    with pytest.raises(AssertionError):
        validate_summary(payload, candidates)


def test_summary_has_no_internal_or_sensitive_fields() -> None:
    payload = {
        "selected_skill": "analyze",
        "ordered_skills": ["analyze"],
        "confidence": 0.5,
        "degraded": True,
    }
    serialized = json.dumps(payload)

    for forbidden in ("route_token", "turn_token", "prompt", "PluginData", "candidate description", "C:\\work"):
        assert forbidden not in serialized


def test_html_route_receipt_remains_the_final_non_empty_line() -> None:
    response = "Normal answer\n{" + '"selected_skill":"analyze"' + "}\n<!-- skilltree-route-decision:{} -->\n"

    non_empty_lines = [line for line in response.splitlines() if line.strip()]
    assert non_empty_lines[-1].startswith("<!-- skilltree-route-decision:")
