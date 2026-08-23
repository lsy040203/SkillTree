"""Pure validation for model-produced, not-yet-approved memory candidates."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any
from uuid import UUID

from skilltree.core.sanitize import sanitize_description


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class MemoryCandidateSchemaError(ValueError):
    """Raised when a MemoryExtractionCandidate crosses a P2 safety boundary."""

    def __init__(self) -> None:
        super().__init__("invalid_schema")


def normalize_memory_extraction_candidate(
    candidate: object,
    *,
    available_skill_names: set[str],
    available_event_ids: set[str],
) -> dict[str, object]:
    """Return a validated copy; this function never persists or approves data."""
    if not isinstance(candidate, dict) or set(candidate) != {
        "schema_version", "profile_fields", "procedural_candidates"
    } or candidate["schema_version"] != "skilltree/v1":
        raise MemoryCandidateSchemaError()
    if not _valid_name_set(available_skill_names) or not _valid_uuid_set(available_event_ids):
        raise MemoryCandidateSchemaError()
    result = deepcopy(candidate)
    profile_fields = result["profile_fields"]
    procedural_candidates = result["procedural_candidates"]
    if not isinstance(profile_fields, list) or len(profile_fields) > 8:
        raise MemoryCandidateSchemaError()
    if not isinstance(procedural_candidates, list) or len(procedural_candidates) > 3:
        raise MemoryCandidateSchemaError()
    profile_handles: set[tuple[str, str]] = set()
    for field in profile_fields:
        _validate_profile_field(field, available_event_ids)
        handle = (field["namespace"], field["key"])
        if handle in profile_handles:
            raise MemoryCandidateSchemaError()
        profile_handles.add(handle)
    for procedure in procedural_candidates:
        _normalize_procedure_candidate(procedure, available_skill_names, available_event_ids)
    return result


def _validate_profile_field(value: object, available_event_ids: set[str]) -> None:
    required = {"namespace", "key", "value", "confidence", "reason"}
    optional = required | {"source_kind", "evidence_event_ids"}
    if not isinstance(value, dict) or not (required <= set(value) <= optional):
        raise MemoryCandidateSchemaError()
    namespace, key, text, confidence, reason = (
        value["namespace"], value["key"], value["value"], value["confidence"], value["reason"]
    )
    if (
        namespace not in {"identity", "preference"}
        or not _valid_identifier(key)
        or not _valid_text(text, 256, required=True)
        or not _valid_probability(confidence)
        or not _valid_text(reason, 300, required=True)
    ):
        raise MemoryCandidateSchemaError()
    if "source_kind" in value and value["source_kind"] not in {
        "durable_preference_statement", "repeated_pattern", "markdown_import",
    }:
        raise MemoryCandidateSchemaError()
    if "evidence_event_ids" in value:
        _validate_evidence_event_ids(value["evidence_event_ids"], available_event_ids)


def _normalize_procedure_candidate(
    value: object,
    available_skill_names: set[str],
    available_event_ids: set[str],
) -> None:
    required = {
        "task_type", "rule", "why", "applies_to", "strength", "when", "recommended_skill_names",
        "ordering_constraints", "avoid_when", "evidence_event_ids",
    }
    optional = required | {"importance_prior", "scenario_key", "scenario_label", "outcome_evidence_kind"}
    if not isinstance(value, dict) or not (required <= set(value) <= optional):
        raise MemoryCandidateSchemaError()
    value.setdefault("importance_prior", 0.5)
    if (
        not _valid_identifier(value["task_type"])
        or not _valid_text(value["rule"], 500, required=True)
        or not _valid_text(value["why"], 200, required=True)
        or not _valid_text(value["applies_to"], 80, required=True)
        or value["strength"] not in {"weak", "strong"}
        or not _valid_probability(value["importance_prior"])
        or not _valid_text(value["when"], 300, required=False)
        or not _valid_text(value["avoid_when"], 300, required=False)
    ):
        raise MemoryCandidateSchemaError()
    if "scenario_key" in value and not _valid_identifier(value["scenario_key"]):
        raise MemoryCandidateSchemaError()
    if "scenario_label" in value and not _valid_text(value["scenario_label"], 120, required=False):
        raise MemoryCandidateSchemaError()
    if "outcome_evidence_kind" in value and value["outcome_evidence_kind"] not in {
        "accepted_response", "successful_execution", "successful_delivery", "none",
    }:
        raise MemoryCandidateSchemaError()
    recommended = value["recommended_skill_names"]
    if (
        not isinstance(recommended, list)
        or len(recommended) > 3
        or len(set(recommended)) != len(recommended)
        or any(not isinstance(name, str) or name not in available_skill_names for name in recommended)
    ):
        raise MemoryCandidateSchemaError()
    constraints = value["ordering_constraints"]
    if not isinstance(constraints, list) or len(constraints) > 3:
        raise MemoryCandidateSchemaError()
    pairs: set[tuple[str, str]] = set()
    for pair in constraints:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(name, str) or name not in available_skill_names for name in pair)
            or pair[0] == pair[1]
            or tuple(pair) in pairs
        ):
            raise MemoryCandidateSchemaError()
        pairs.add(tuple(pair))
    _validate_evidence_event_ids(value["evidence_event_ids"], available_event_ids)


def _validate_evidence_event_ids(evidence_event_ids: object, available_event_ids: set[str]) -> None:
    if (
        not isinstance(evidence_event_ids, list)
        or len(evidence_event_ids) > 10
        or len(set(evidence_event_ids)) != len(evidence_event_ids)
        or any(not isinstance(event_id, str) or event_id not in available_event_ids for event_id in evidence_event_ids)
    ):
        raise MemoryCandidateSchemaError()


def _valid_identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _valid_probability(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0.0 <= value <= 1.0


def _valid_text(value: object, limit: int, *, required: bool) -> bool:
    if not isinstance(value, str) or len(value) > limit or (required and not value):
        return False
    return sanitize_description(value).state != "rejected"


def _valid_name_set(values: set[str]) -> bool:
    return all(isinstance(value, str) and value for value in values)


def _valid_uuid_set(values: set[str]) -> bool:
    return all(_canonical_uuid(value) for value in values)


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False
