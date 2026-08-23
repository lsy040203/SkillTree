from __future__ import annotations

import pytest

from skilltree.memory_candidates import MemoryCandidateSchemaError, normalize_memory_extraction_candidate


def test_normalizes_a_bounded_memory_extraction_candidate_without_persisting_it() -> None:
    candidate = {
        "schema_version": "skilltree/v1",
        "profile_fields": [
            {
                "namespace": "preference",
                "key": "explanation_style",
                "value": "use concise Chinese explanations",
                "confidence": 0.9,
                "reason": "user consistently asks in Chinese",
            }
        ],
        "procedural_candidates": [
            {
                "task_type": "repository_analysis",
                "rule": "Scan trusted Skills before selecting an analysis workflow.",
                "why": "Avoid untrusted Skill instructions.",
                "applies_to": "skill_routing",
                "strength": "weak",
                "when": "when route candidates are available",
                "recommended_skill_names": ["analyze"],
                "ordering_constraints": [["analyze", "lsp"]],
                "avoid_when": "when no trusted candidate exists",
                "evidence_event_ids": ["9bb84d94-1cf2-4bea-865c-5b4073b4c524"],
            }
        ],
    }

    normalized = normalize_memory_extraction_candidate(
        candidate,
        available_skill_names={"analyze", "lsp"},
        available_event_ids={"9bb84d94-1cf2-4bea-865c-5b4073b4c524"},
    )

    assert normalized["procedural_candidates"][0]["importance_prior"] == 0.5
    assert candidate["procedural_candidates"][0].get("importance_prior") is None


def test_accepts_profile_provenance_and_procedure_scenario_metadata() -> None:
    candidate = _minimal_candidate()
    candidate["profile_fields"][0].update(
        source_kind="durable_preference_statement",
        evidence_event_ids=["9bb84d94-1cf2-4bea-865c-5b4073b4c524"],
    )
    candidate["procedural_candidates"][0].update(
        scenario_key="skilltree_p5",
        scenario_label="P5 memory design",
        outcome_evidence_kind="successful_execution",
    )

    normalized = normalize_memory_extraction_candidate(
        candidate,
        available_skill_names={"analyze"},
        available_event_ids={"9bb84d94-1cf2-4bea-865c-5b4073b4c524"},
    )

    assert normalized["profile_fields"][0]["source_kind"] == "durable_preference_statement"
    assert normalized["procedural_candidates"][0]["scenario_key"] == "skilltree_p5"


@pytest.mark.parametrize(
    ("mutate", "available_skills", "available_events"),
    [
        (lambda value: value["profile_fields"].append(value["profile_fields"][0]), {"analyze", "lsp"}, set()),
        (lambda value: value["procedural_candidates"][0].update(recommended_skill_names=["untrusted"]), {"analyze", "lsp"}, set()),
        (lambda value: value["profile_fields"][0].update(value="token=ghp_abcdefghijklmnopqrstuvwxyz1234567890"), {"analyze", "lsp"}, set()),
        (lambda value: value["procedural_candidates"][0].update(evidence_event_ids=["not-a-uuid"]), {"analyze", "lsp"}, set()),
    ],
)
def test_rejects_unsafe_or_out_of_scope_memory_candidates(mutate, available_skills, available_events) -> None:
    candidate = _minimal_candidate()
    mutate(candidate)

    with pytest.raises(MemoryCandidateSchemaError, match="invalid_schema"):
        normalize_memory_extraction_candidate(
            candidate,
            available_skill_names=available_skills,
            available_event_ids=available_events,
        )


def _minimal_candidate() -> dict[str, object]:
    return {
        "schema_version": "skilltree/v1",
        "profile_fields": [
            {
                "namespace": "identity",
                "key": "language",
                "value": "Chinese",
                "confidence": 0.8,
                "reason": "user preference",
            }
        ],
        "procedural_candidates": [
            {
                "task_type": "repository_analysis",
                "rule": "Use trusted analysis skills.",
                "why": "Trust boundary.",
                "applies_to": "skill_routing",
                "strength": "weak",
                "when": "during routing",
                "recommended_skill_names": ["analyze"],
                "ordering_constraints": [],
                "avoid_when": "",
                "evidence_event_ids": [],
            }
        ],
    }
