"""Bounded, injected candidate extractors for P5 memory."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen as _urlopen

from skilltree.core.evidence import EvidenceBundle
from skilltree.core.memory_candidates import MemoryCandidateSchemaError, normalize_memory_extraction_candidate
from skilltree.core.sanitize import sanitize_description


class CandidateLLMError(RuntimeError):
    """A provider/configuration failure that must not mutate active memory."""


@dataclass(frozen=True)
class CandidateLLMConfig:
    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> "CandidateLLMConfig":
        values = os.environ if environ is None else environ
        api_key = values.get("SKILLTREE_MEMORY_API_KEY", "")
        model = values.get("SKILLTREE_MEMORY_MODEL", "")
        base_url = values.get("SKILLTREE_MEMORY_BASE_URL", cls.base_url).rstrip("/")
        timeout_value = values.get("SKILLTREE_MEMORY_TIMEOUT_SECONDS", "30")
        if not api_key or not model or not _valid_base_url(base_url):
            raise CandidateLLMError("configuration_invalid")
        try:
            timeout_seconds = float(timeout_value)
        except (TypeError, ValueError):
            raise CandidateLLMError("configuration_invalid") from None
        if not 1.0 <= timeout_seconds <= 120.0:
            raise CandidateLLMError("configuration_invalid")
        return cls(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )


class CandidateLLM(Protocol):
    """A caller-supplied candidate generator; it has no storage authority."""

    def generate_memory_candidate(self, prompt: dict[str, object]) -> object: ...


class OpenAICompatibleCandidateLLM:
    """Standard-library provider for one bounded candidate-generation request."""

    _MAX_RESPONSE_BYTES = 256 * 1024

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
        urlopen: object = _urlopen,
    ) -> None:
        if not api_key or not model or not _valid_base_url(base_url.rstrip("/")):
            raise CandidateLLMError("configuration_invalid")
        if not 1.0 <= timeout_seconds <= 120.0:
            raise CandidateLLMError("configuration_invalid")
        self._config = CandidateLLMConfig(
            api_key=api_key,
            model=model,
            base_url=base_url.rstrip("/"),
            timeout_seconds=timeout_seconds,
        )
        self._urlopen = urlopen

    @classmethod
    def from_environment(
        cls,
        *,
        environ: dict[str, str] | None = None,
        urlopen: object = _urlopen,
    ) -> "OpenAICompatibleCandidateLLM":
        config = CandidateLLMConfig.from_environment(environ)
        return cls(
            api_key=config.api_key,
            model=config.model,
            base_url=config.base_url,
            timeout_seconds=config.timeout_seconds,
            urlopen=urlopen,
        )

    def generate_memory_candidate(self, prompt: dict[str, object]) -> object:
        if not isinstance(prompt, dict):
            raise CandidateLLMError("request_invalid")
        safe_prompt = _sanitize_prompt(prompt)
        request_body = {
            "model": self._config.model,
            "messages": [
                {
                    "role": "system",
                    "content": _candidate_system_prompt(prompt.get("kind")),
                },
                {
                    "role": "user",
                    "content": json.dumps(safe_prompt, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            "temperature": 0,
            # Some OpenAI-compatible reasoning models put all output in
            # reasoning_content and leave message.content empty by default.
            # Candidate extraction requires a bounded JSON content response.
            "enable_thinking": False,
            "max_tokens": 1024,
        }
        encoded = json.dumps(request_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = Request(
            f"{self._config.base_url}/chat/completions",
            data=encoded,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._config.api_key}",
            },
            method="POST",
        )
        try:
            with self._urlopen(request, timeout=self._config.timeout_seconds) as response:
                raw = response.read(self._MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError):
            raise CandidateLLMError("transport_failed") from None
        if not isinstance(raw, bytes) or len(raw) > self._MAX_RESPONSE_BYTES:
            raise CandidateLLMError("response_too_large")
        try:
            envelope = json.loads(raw.decode("utf-8"))
            content = envelope["choices"][0]["message"]["content"]
            candidate = json.loads(content) if isinstance(content, str) else content
        except (KeyError, IndexError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise CandidateLLMError("response_invalid") from None
        if (
            not isinstance(candidate, dict)
            or set(candidate) != {"schema_version", "profile_fields", "procedural_candidates"}
        ):
            raise CandidateLLMError("response_invalid")
        return candidate


class ProfileExtractor:
    """Generate only untrusted Profile candidates from durable user statements."""

    def __init__(self, *, llm: CandidateLLM) -> None:
        self._llm = llm

    def extract(self, bundle: EvidenceBundle) -> dict[str, object]:
        if not bundle.durable_preference_statements:
            return _empty_candidate()
        prompt = {
            "schema_version": "skilltree-memory-extractor/v1",
            "kind": "profile",
            "run_id": bundle.run_id,
            "durable_preference_statements": list(bundle.durable_preference_statements),
            "evidence_event_ids": list(bundle.evidence_event_ids),
        }
        candidate = _parse_candidate(self._llm.generate_memory_candidate(prompt))
        if candidate["procedural_candidates"]:
            raise CandidateLLMError("response_invalid")
        return _normalize(_repair_profile_candidate(candidate), bundle)


class ProcedureExtractor:
    """Generate only untrusted Procedure candidates from eligible run outcomes."""

    def __init__(self, *, llm: CandidateLLM) -> None:
        self._llm = llm

    def extract(self, bundle: EvidenceBundle) -> dict[str, object]:
        if bundle.outcome_evidence_kind not in {
            "accepted_response", "successful_execution", "successful_delivery",
        }:
            return _empty_candidate()
        if bundle.route_degraded or bundle.coverage_state != "observed" or not bundle.observed_tool_steps:
            return _empty_candidate()
        prompt = {
            "schema_version": "skilltree-memory-extractor/v1",
            "kind": "procedure",
            "run_id": bundle.run_id,
            "task_type": bundle.task_type,
            "scenario_key": bundle.scenario_key,
            "scenario_label": bundle.scenario_label,
            "recommended_skills": list(bundle.recommended_skills),
            "observed_tool_steps": list(bundle.observed_tool_steps),
            "observed_tool_chain": list(bundle.observed_tool_chain),
            "outcome": bundle.outcome,
            "outcome_evidence_kind": bundle.outcome_evidence_kind,
            "coverage_state": bundle.coverage_state,
            "route_degraded": bundle.route_degraded,
            "evidence_event_ids": list(bundle.evidence_event_ids),
            # Keep the evidence IDs as an explicit closed set.  Small models
            # otherwise tend to synthesize UUID-looking IDs from summaries.
            "allowed_evidence_event_ids": list(bundle.evidence_event_ids),
        }
        candidate = _parse_candidate(self._llm.generate_memory_candidate(prompt))
        if candidate["profile_fields"]:
            raise CandidateLLMError("response_invalid")
        normalized = _normalize(_repair_llm_candidate(candidate), bundle)
        for procedure in normalized["procedural_candidates"]:
            if not isinstance(procedure, dict):
                raise CandidateLLMError("response_invalid")
            if procedure.get("outcome_evidence_kind", bundle.outcome_evidence_kind) != bundle.outcome_evidence_kind:
                raise CandidateLLMError("response_invalid")
        return normalized


def extract_memory_candidates(
    bundle: EvidenceBundle, *, llm: CandidateLLM
) -> tuple[dict[str, object], ...]:
    """Generate profile/procedure proposals without persistence or approval."""
    results: list[dict[str, object]] = []
    profile = ProfileExtractor(llm=llm).extract(bundle)
    if profile["profile_fields"]:
        results.append(profile)
    procedure = ProcedureExtractor(llm=llm).extract(bundle)
    if procedure["procedural_candidates"]:
        results.append(procedure)
    return tuple(results)


def _empty_candidate() -> dict[str, object]:
    return {
        "schema_version": "skilltree/v1",
        "profile_fields": [],
        "procedural_candidates": [],
    }


def _parse_candidate(value: object) -> dict[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise CandidateLLMError("response_invalid") from None
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "profile_fields", "procedural_candidates"
    }:
        raise CandidateLLMError("response_invalid")
    if value["schema_version"] == "skilltree-memory-extractor/v1":
        value = dict(value)
        value["schema_version"] = "skilltree/v1"
    if value["schema_version"] != "skilltree/v1":
        raise CandidateLLMError("response_invalid")
    return value


def _normalize(candidate: dict[str, object], bundle: EvidenceBundle) -> dict[str, object]:
    try:
        return normalize_memory_extraction_candidate(
            candidate,
            available_skill_names=set(bundle.recommended_skills),
            available_event_ids=set(bundle.evidence_event_ids),
        )
    except MemoryCandidateSchemaError as error:
        raise CandidateLLMError("response_invalid") from error


def _sanitize_prompt(value: object) -> object:
    """Sanitize every textual value at the outbound provider boundary."""
    if isinstance(value, str):
        sanitized = sanitize_description(value)
        return sanitized.value if sanitized.state != "rejected" else "[REDACTED]"
    if isinstance(value, dict):
        return {key: _sanitize_prompt(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_prompt(item) for item in value]
    return value


def _repair_llm_candidate(candidate: dict[str, object]) -> dict[str, object]:
    """Drop operation-order labels that cannot be represented as Skill pairs.

    The persisted candidate contract intentionally remains strict.  Some LLMs
    interpret ``ordering_constraints`` as execution-operation labels such as
    ``read_source_before_run_python``.  Those labels are already represented
    by the procedure rule/when text, but are not valid Skill-to-Skill edges.
    Repair only this bounded, recognizable shape and leave all other malformed
    values for the normal schema validator to reject.
    """
    repaired = deepcopy(candidate)
    procedures = repaired.get("procedural_candidates")
    if not isinstance(procedures, list):
        return repaired
    operation_order = re.compile(r"[a-z][a-z0-9_]*_before_[a-z][a-z0-9_]*\Z")
    for procedure in procedures:
        if not isinstance(procedure, dict):
            continue
        constraints = procedure.get("ordering_constraints")
        if (
            isinstance(constraints, list)
            and constraints
            and all(isinstance(item, str) and operation_order.fullmatch(item) for item in constraints)
        ):
            procedure["ordering_constraints"] = []
    return repaired


def _repair_profile_candidate(candidate: dict[str, object]) -> dict[str, object]:
    """Normalize one bounded provider alias without weakening candidate validation."""
    repaired = deepcopy(candidate)
    profiles = repaired.get("profile_fields")
    if not isinstance(profiles, list):
        return repaired
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        if profile.get("source_kind") in {"durable_preference", "explicit_preference"}:
            profile["source_kind"] = "durable_preference_statement"
        if profile.get("namespace") in {"communication", "format", "style"}:
            profile["namespace"] = "preference"
        # Explicit Profile statements are deliberately not TraceEvent records.
        # Their evidence is available only for the extraction request, so no
        # string supplied by the model can legitimately be an event handle.
        profile["evidence_event_ids"] = []
    return repaired


def _valid_base_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.query and not parsed.fragment


def _candidate_system_prompt(kind: object) -> str:
    common = (
        "Return exactly one JSON object with schema_version exactly skilltree/v1, "
        "profile_fields, and procedural_candidates. "
        "Never include approval, TTL, score, active state, or extra top-level keys. "
    )
    profile = (
        "Extract only an explicit, durable user preference or stable user attribute from "
        "durable_preference_statements. Do not infer a preference from response_feedback, "
        "the assistant response, a project fact, or a one-off instruction. A statement such "
        "as 'this time use a table' is transient, not profile evidence. Return an empty "
        "profile_fields array when durable evidence is absent. "
        "For profile_fields use only objects with namespace, key, value, confidence, "
        "reason, optional source_kind, and optional evidence_event_ids. namespace MUST be "
        "exactly identity or preference. For this explicit-statement request evidence_event_ids "
        "MUST be an empty array: do not place a user statement, summary, or invented ID there. "
        "If source_kind "
        "is present, it MUST be exactly durable_preference_statement, repeated_pattern, "
        "or markdown_import. "
        "For a profile request procedural_candidates must be an empty array."
    )
    procedure = (
        "Extract only a concrete, reusable way of working from the supplied observed evidence. "
        "RouteDecision is not proof that a Skill executed; recommended_skills are only names "
        "available for a candidate and never evidence of use. Do not turn a task title, Skill "
        "description, route recommendation, or the assistant's own response into a procedure. "
        "If route_degraded is true, observed_tool_steps is empty, or coverage_state is not observed, return an empty "
        "procedural_candidates array. An accepted_response outcome may produce only a weak "
        "candidate and must never be described as a verified SOP; successful_execution or "
        "successful_delivery is required for a verified method. A one-off instruction, ordinary fact, personal preference, "
        "generic advice, or read-only confirmation is not a procedure. In particular, a minimal "
        "read-only confirmation such as checking that one file exists must return an empty array. "
        "Create a procedure only when the evidence shows a task-specific method with reusable "
        "steps or constraints, an explicit scope, and a success basis. Prefer an empty array "
        "when the evidence is ambiguous. recommended_skill_names may include only names present "
        "in recommended_skills and only when the observed steps support that association. "
        "The observed_tool_chain contains sanitized summaries and real event IDs; use its "
        "tool_use_id pairing to describe the sequence, and copy only those event IDs into "
        "evidence_event_ids. The allowed_evidence_event_ids field is a closed set: every "
        "output evidence_event_ids value MUST be copied verbatim from that list. Never "
        "invent UUIDs, event IDs, or use summary labels as IDs; if you cannot copy a real "
        "ID exactly, return an empty procedural_candidates array. "
        "For a procedure request profile_fields must be an empty array. "
        "Each procedural candidate MUST contain task_type, rule, why, applies_to, strength, "
        "when, avoid_when, recommended_skill_names, ordering_constraints, and evidence_event_ids. "
        "task_type and applies_to MUST be one non-empty string using lowercase letters, digits, and underscores; "
        "applies_to MUST be one non-empty string, never an array. "
        "strength MUST be exactly \"weak\" or \"strong\", never a number. "
        "rule and why MUST be non-empty strings. when and avoid_when MUST be strings. "
        "recommended_skill_names, ordering_constraints, and evidence_event_ids MUST be arrays. "
        "ordering_constraints MUST contain only two-element arrays of Skill names; never use "
        "operation labels such as read_source_before_run_python there. Put operation ordering "
        "in rule, when, or avoid_when instead. "
        "Optional scenario_key, scenario_label, outcome_evidence_kind, and importance_prior must use their JSON "
        "string/string/string/number types respectively. Return an empty procedural_candidates array when the "
        "evidence does not support a concrete reusable procedure."
    )
    return common + (profile if kind == "profile" else procedure)
