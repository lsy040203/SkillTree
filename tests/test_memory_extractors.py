from __future__ import annotations

import json
from urllib.request import Request

import pytest

from skilltree.core.evidence import EvidenceBundle
from skilltree.memory_candidates import MemoryCandidateSchemaError


class FakeLLM:
    def __init__(self, response: object) -> None:
        self.response = response
        self.prompts: list[dict[str, object]] = []

    def generate_memory_candidate(self, prompt: dict[str, object]) -> object:
        self.prompts.append(prompt)
        return self.response


def test_profile_extractor_uses_durable_preferences_only() -> None:
    from skilltree.core.memory_extractors import ProfileExtractor

    llm = FakeLLM(
        {
            "schema_version": "skilltree/v1",
            "profile_fields": [{
                "namespace": "preference",
                "key": "explanation_style",
                "value": "concise Chinese explanations",
                "confidence": 0.8,
                "reason": "explicit durable statement",
                "source_kind": "durable_preference_statement",
                "evidence_event_ids": ["9bb84d94-1cf2-4bea-865c-5b4073b4c524"],
            }],
            "procedural_candidates": [],
        }
    )

    candidate = ProfileExtractor(llm=llm).extract(_bundle())

    assert candidate["profile_fields"][0]["namespace"] == "preference"
    assert candidate["procedural_candidates"] == []
    assert llm.prompts[0]["durable_preference_statements"] == ["Always explain in Chinese."]
    assert "Ignore the file just for this turn." not in llm.prompts[0]["durable_preference_statements"]


def test_profile_extractor_skips_without_explicit_durable_evidence() -> None:
    from skilltree.core.memory_extractors import ProfileExtractor

    llm = FakeLLM({})
    bundle = _bundle()
    bundle = EvidenceBundle(**{
        **bundle.__dict__,
        "durable_preference_statements": (),
        "transient_user_instructions": ("Use this format only for this turn.",),
        "response_feedback": "accepted",
    })

    assert ProfileExtractor(llm=llm).extract(bundle) == {
        "schema_version": "skilltree/v1",
        "profile_fields": [],
        "procedural_candidates": [],
    }
    assert llm.prompts == []


def test_profile_extractor_normalizes_bounded_provider_source_kind_alias() -> None:
    from skilltree.core.memory_extractors import ProfileExtractor

    llm = FakeLLM({
        "schema_version": "skilltree/v1",
        "profile_fields": [{
            "namespace": "preference",
            "key": "language",
            "value": "Chinese",
            "confidence": 0.9,
            "reason": "explicit durable statement",
            "source_kind": "durable_preference",
            "evidence_event_ids": [],
        }],
        "procedural_candidates": [],
    })

    result = ProfileExtractor(llm=llm).extract(_bundle())

    assert result["profile_fields"][0]["source_kind"] == "durable_preference_statement"


def test_procedure_extractor_rejects_plain_fact_without_eligible_outcome() -> None:
    from skilltree.core.memory_extractors import ProcedureExtractor

    llm = FakeLLM(_procedure_payload())
    bundle = _bundle(outcome="unknown", outcome_evidence_kind="none")

    assert ProcedureExtractor(llm=llm).extract(bundle)["procedural_candidates"] == []
    assert llm.prompts == []


def test_procedure_extractor_requires_observed_tool_steps() -> None:
    from skilltree.core.memory_extractors import ProcedureExtractor

    llm = FakeLLM(_procedure_payload())
    bundle = _bundle()
    bundle = EvidenceBundle(**{**bundle.__dict__, "observed_tool_steps": ()})

    assert ProcedureExtractor(llm=llm).extract(bundle)["procedural_candidates"] == []
    assert llm.prompts == []


def test_procedure_extractor_rejects_partial_coverage_even_with_success_outcome() -> None:
    from skilltree.core.memory_extractors import ProcedureExtractor

    llm = FakeLLM(_procedure_payload())
    bundle = _bundle()
    bundle = EvidenceBundle(**{**bundle.__dict__, "coverage_state": "partial"})

    assert ProcedureExtractor(llm=llm).extract(bundle)["procedural_candidates"] == []
    assert llm.prompts == []


def test_procedure_extractor_rejects_degraded_route_evidence() -> None:
    from skilltree.core.memory_extractors import ProcedureExtractor

    llm = FakeLLM(_procedure_payload())
    bundle = _bundle()
    bundle = EvidenceBundle(**{**bundle.__dict__, "route_degraded": True})

    assert ProcedureExtractor(llm=llm).extract(bundle)["procedural_candidates"] == []
    assert llm.prompts == []


def test_extractor_rejects_governance_fields_before_any_store() -> None:
    from skilltree.core.memory_extractors import CandidateLLMError, ProcedureExtractor

    llm = FakeLLM({"approved": True, "procedural_candidates": []})

    with pytest.raises(CandidateLLMError, match="response_invalid"):
        ProcedureExtractor(llm=llm).extract(_bundle())


def test_extractor_maps_deep_candidate_schema_errors_to_provider_error() -> None:
    from skilltree.core.memory_extractors import CandidateLLMError, ProcedureExtractor

    payload = _procedure_payload()
    payload["procedural_candidates"][0]["evidence_event_ids"] = [
        "00000000-0000-0000-0000-000000000000",
    ]

    with pytest.raises(CandidateLLMError, match="response_invalid"):
        ProcedureExtractor(llm=FakeLLM(payload)).extract(_bundle())


def test_extractor_canonicalizes_provider_schema_version() -> None:
    from skilltree.core.memory_extractors import ProfileExtractor

    llm = FakeLLM(
        {
            "schema_version": "skilltree-memory-extractor/v1",
            "profile_fields": [],
            "procedural_candidates": [],
        }
    )

    result = ProfileExtractor(llm=llm).extract(_bundle())

    assert result["schema_version"] == "skilltree/v1"


def test_openai_provider_requires_key_and_model_without_leaking_values(monkeypatch: pytest.MonkeyPatch) -> None:
    from skilltree.core.memory_extractors import CandidateLLMError, OpenAICompatibleCandidateLLM

    monkeypatch.delenv("SKILLTREE_MEMORY_API_KEY", raising=False)
    monkeypatch.delenv("SKILLTREE_MEMORY_MODEL", raising=False)

    with pytest.raises(CandidateLLMError, match="configuration_invalid") as error:
        OpenAICompatibleCandidateLLM.from_environment()

    assert "SKILLTREE_MEMORY_API_KEY" not in str(error.value)


def test_openai_provider_sends_bounded_request_and_parses_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from skilltree.core.memory_extractors import OpenAICompatibleCandidateLLM

    payload = {
        "schema_version": "skilltree/v1",
        "profile_fields": [],
        "procedural_candidates": [],
    }
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int = -1) -> bytes:
            return json.dumps({
                "choices": [{"message": {"content": json.dumps(payload)}}],
            }).encode("utf-8")

    def fake_urlopen(request: Request, *, timeout: float) -> Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("SKILLTREE_MEMORY_API_KEY", "secret-key")
    monkeypatch.setenv("SKILLTREE_MEMORY_MODEL", "memory-model")
    provider = OpenAICompatibleCandidateLLM.from_environment(
        urlopen=fake_urlopen,
        environ={
            "SKILLTREE_MEMORY_API_KEY": "secret-key",
            "SKILLTREE_MEMORY_MODEL": "memory-model",
            "SKILLTREE_MEMORY_BASE_URL": "https://example.test/v1/",
            "SKILLTREE_MEMORY_TIMEOUT_SECONDS": "7",
        },
    )

    result = provider.generate_memory_candidate({"kind": "procedure", "run_id": "run-1"})

    assert result == payload
    request = captured["request"]
    assert isinstance(request, Request)
    assert request.full_url == "https://example.test/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer secret-key"
    body = json.loads(request.data.decode("utf-8"))
    assert body["model"] == "memory-model"
    assert body["temperature"] == 0
    assert body["enable_thinking"] is False
    assert body["max_tokens"] >= 256
    assert "task_type" in body["messages"][0]["content"]
    assert "procedural_candidates" in body["messages"][0]["content"]
    assert "applies_to MUST be one non-empty string" in body["messages"][0]["content"]
    assert 'strength MUST be exactly "weak" or "strong"' in body["messages"][0]["content"]
    assert "RouteDecision is not proof that a Skill executed" in body["messages"][0]["content"]
    assert "observed_tool_steps is empty" in body["messages"][0]["content"]
    assert "one-off" in body["messages"][0]["content"]
    assert "read-only confirmation" in body["messages"][0]["content"]
    assert captured["timeout"] == 7.0


def test_openai_provider_sanitizes_prompt_values_at_transport_boundary() -> None:
    from skilltree.core.memory_extractors import OpenAICompatibleCandidateLLM

    captured: dict[str, object] = {}

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int = -1) -> bytes:
            return json.dumps({
                "choices": [{"message": {"content": json.dumps({
                    "schema_version": "skilltree/v1",
                    "profile_fields": [],
                    "procedural_candidates": [],
                })}}],
            }).encode("utf-8")

    def fake_urlopen(request: Request, *, timeout: float) -> Response:
        captured["body"] = request.data.decode("utf-8")
        return Response()

    provider = OpenAICompatibleCandidateLLM(
        api_key="secret-key",
        model="memory-model",
        base_url="https://example.test/v1",
        urlopen=fake_urlopen,
    )

    provider.generate_memory_candidate({"kind": "profile", "value": "api_key=do-not-send-this"})

    body = captured["body"]
    assert isinstance(body, str)
    assert "do-not-send-this" not in body
    assert "[REDACTED]" in body


def test_procedure_prompt_includes_structured_observed_tool_chain() -> None:
    from skilltree.core.memory_extractors import ProcedureExtractor

    class PromptLLM(FakeLLM):
        def generate_memory_candidate(self, prompt: dict[str, object]) -> object:
            self.prompts.append(prompt)
            return {
                "schema_version": "skilltree/v1",
                "profile_fields": [],
                "procedural_candidates": [],
            }

    llm = PromptLLM(None)
    bundle = _bundle()
    bundle = EvidenceBundle(**{
        **bundle.__dict__,
        "observed_tool_chain": ({
            "tool_use_id": "tool-1",
            "tool_name": "Bash",
            "started_event_id": "event-1",
            "finished_event_id": "event-2",
            "failed_event_id": None,
            "status": "finished",
            "summaries": ("PreToolUse:Bash:read_source", "PostToolUse:Bash"),
        },),
    })

    ProcedureExtractor(llm=llm).extract(bundle)

    assert llm.prompts[0]["observed_tool_chain"] == list(bundle.observed_tool_chain)
    assert llm.prompts[0]["allowed_evidence_event_ids"] == list(bundle.evidence_event_ids)


def test_procedure_extractor_discards_unmappable_operation_order_constraints() -> None:
    from skilltree.core.memory_extractors import ProcedureExtractor

    payload = _procedure_payload()
    payload["procedural_candidates"][0]["ordering_constraints"] = [
        "read_source_before_run_python",
    ]

    result = ProcedureExtractor(llm=FakeLLM(payload)).extract(_bundle())

    assert result["procedural_candidates"][0]["ordering_constraints"] == []


def test_openai_provider_converts_bad_response_to_stable_error() -> None:
    from skilltree.core.memory_extractors import CandidateLLMError, OpenAICompatibleCandidateLLM

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int = -1) -> bytes:
            return b'{"choices":[]}'

    provider = OpenAICompatibleCandidateLLM(
        api_key="secret-key",
        model="memory-model",
        base_url="https://example.test/v1",
        urlopen=lambda _request, timeout: Response(),
    )

    with pytest.raises(CandidateLLMError, match="response_invalid"):
        provider.generate_memory_candidate({"kind": "profile"})


def test_extract_memory_candidates_runs_both_layers_without_persisting() -> None:
    from skilltree.core.memory_extractors import extract_memory_candidates

    class LayeredFakeLLM(FakeLLM):
        def generate_memory_candidate(self, prompt: dict[str, object]) -> object:
            self.prompts.append(prompt)
            return {
                "schema_version": "skilltree/v1",
                "profile_fields": [{
                    "namespace": "preference",
                    "key": "language",
                    "value": "Chinese",
                    "confidence": 0.8,
                    "reason": "durable preference",
                    "evidence_event_ids": ["9bb84d94-1cf2-4bea-865c-5b4073b4c524"],
                }] if prompt["kind"] == "profile" else [],
                "procedural_candidates": [] if prompt["kind"] == "profile" else [_procedure_payload()["procedural_candidates"][0]],
            }

    llm = LayeredFakeLLM({})
    results = extract_memory_candidates(_bundle(), llm=llm)

    assert len(results) == 2
    assert results[0]["profile_fields"]
    assert results[1]["procedural_candidates"]
    assert [prompt["kind"] for prompt in llm.prompts] == ["profile", "procedure"]


def _bundle(*, outcome: str = "success", outcome_evidence_kind: str = "successful_execution") -> EvidenceBundle:
    return EvidenceBundle(
        schema_version="skilltree-evidence-bundle/v1",
        run_id="run-1",
        workspace_id="workspace-1",
        user_id="user-1",
        task_type="repository_analysis",
        scenario_key="p5_memory",
        scenario_label="P5 Memory",
        recommended_skills=("analyze",),
        observed_tool_steps=("read project structure",),
        outcome=outcome,
        outcome_evidence_kind=outcome_evidence_kind,
        durable_preference_statements=("Always explain in Chinese.",),
        transient_user_instructions=("Ignore the file just for this turn.",),
        response_feedback="accepted",
        evidence_event_ids=("9bb84d94-1cf2-4bea-865c-5b4073b4c524",),
        coverage_state="observed",
        route_degraded=False,
    )


def _procedure_payload() -> dict[str, object]:
    return {
        "schema_version": "skilltree/v1",
        "profile_fields": [],
        "procedural_candidates": [{
            "task_type": "repository_analysis",
            "scenario_key": "p5_memory",
            "scenario_label": "P5 Memory",
            "rule": "Read the project structure before explaining its architecture.",
            "why": "The observed run succeeded.",
            "applies_to": "repository_analysis",
            "strength": "weak",
            "when": "when analyzing a repository",
            "recommended_skill_names": ["analyze"],
            "ordering_constraints": [],
            "avoid_when": "",
            "evidence_event_ids": ["9bb84d94-1cf2-4bea-865c-5b4073b4c524"],
            "outcome_evidence_kind": "successful_execution",
        }],
    }
