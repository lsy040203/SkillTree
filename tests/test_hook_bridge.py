from __future__ import annotations

import hashlib

from skilltree.hook_bridge import (
    parse_stop_route_commit,
    prepare_user_prompt_context,
    project_route_envelope,
    validate_internal_route_envelope,
)


def test_user_prompt_bridge_exposes_only_a_valid_route_envelope() -> None:
    captured: dict[str, object] = {}

    def prepare_route(workspace_id: str, session_id_hash: str, prompt: str) -> dict[str, object]:
        captured.update(workspace_id=workspace_id, session_id_hash=session_id_hash, prompt=prompt)
        return {
            "schema_version": "skilltree-route-envelope/v1",
            "route_token": "a" * 43,
            "expires_at": "2026-08-15T00:05:00.000Z",
            "candidate_snapshot_hash": _hash("candidates"),
            "degraded": False,
            "candidates": [{"name": "analyze", "description": "Analyze repositories", "content_hash": _hash("analyze")}],
        }

    context = prepare_user_prompt_context(
        {"prompt": "Analyze this repository", "session_id": "session-1", "cwd": "C:/work/repository"},
        {"diagnostic_state": "degraded"},
        prepare_route,
    )

    assert context is not None
    assert context["candidates"][0]["name"] == "analyze"
    assert context["degraded"] is False
    assert "path" not in str(context)
    assert "Analyze this repository" not in str(context)
    assert captured == {
        "workspace_id": _hash("C:/work/repository"),
        "session_id_hash": _hash("session-1"),
        "prompt": "Analyze this repository",
    }


def test_user_prompt_bridge_accepts_the_full_registry_capacity() -> None:
    candidates = [
        {"name": f"skill-{index:03d}", "description": "A visible skill", "content_hash": _hash(str(index))}
        for index in range(10)
    ]
    envelope = {
        "schema_version": "skilltree-route-envelope/v1",
        "route_token": "a" * 43,
        "expires_at": "2026-08-15T00:05:00.000Z",
        "candidate_snapshot_hash": _hash("catalog"),
        "degraded": False,
        "candidates": candidates,
    }

    assert validate_internal_route_envelope(envelope)
    projected = project_route_envelope(envelope)
    assert len(projected["candidates"]) == 10
    assert projected["degraded"] is False


def test_user_prompt_bridge_rejects_non_boolean_degraded() -> None:
    envelope = {
        "schema_version": "skilltree-route-envelope/v1",
        "route_token": "a" * 43,
        "expires_at": "2026-08-15T00:05:00.000Z",
        "candidate_snapshot_hash": _hash("catalog"),
        "degraded": "false",
        "candidates": [{"name": "analyze", "description": "Analyze repositories", "content_hash": _hash("analyze")}],
    }

    assert not validate_internal_route_envelope(envelope)


def test_user_prompt_bridge_fails_open_when_runtime_is_not_ready_or_payload_is_invalid() -> None:
    calls = 0

    def prepare_route(workspace_id: str, session_id_hash: str, prompt: str) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {}

    assert prepare_user_prompt_context(
        {"prompt": "anything", "session_id": "session-1", "cwd": "C:/work"},
        {"diagnostic_state": "failed"},
        prepare_route,
    ) is None
    assert prepare_user_prompt_context(
        {"prompt": "anything", "session_id": "session-1"},
        {"diagnostic_state": "ready"},
        prepare_route,
    ) is None
    assert calls == 0


def test_stop_parser_accepts_exactly_one_final_route_decision_comment() -> None:
    message = (
        "已完成路由。\n"
        "<!-- skilltree-route-decision:{\"schema_version\":\"skilltree-route-commit/v1\",\"route_token\":\""
        + "a" * 43
        + "\",\"decision\":{\"schema_version\":\"skilltree/v1\"}} -->"
    )

    commit = parse_stop_route_commit(message, "C:/work/repository", "session-1")

    assert commit == {
        "schema_version": "skilltree-route-commit/v1",
        "route_token": "a" * 43,
        "workspace_id": _hash("C:/work/repository"),
        "session_id_hash": _hash("session-1"),
        "decision": {"schema_version": "skilltree/v1"},
    }


def test_stop_parser_rejects_missing_repeated_nonfinal_or_oversized_comments() -> None:
    marker = "<!-- skilltree-route-decision:{\"schema_version\":\"skilltree-route-commit/v1\",\"route_token\":\"" + "a" * 43 + "\",\"decision\":{}} -->"

    assert parse_stop_route_commit("ordinary answer", "C:/work", "session-1") is None
    assert parse_stop_route_commit(f"{marker}\nvisible answer", "C:/work", "session-1") is None
    assert parse_stop_route_commit(f"{marker}\n{marker}", "C:/work", "session-1") is None
    assert parse_stop_route_commit("<!-- skilltree-route-decision:" + "a" * 4097 + " -->", "C:/work", "session-1") is None


def test_stop_parser_accepts_visible_json_summary_when_host_strips_html_comment() -> None:
    message = """推荐 Skill：`analyze`

```json
{
  "selected_skill": "analyze",
  "ordered_skills": ["analyze"],
  "confidence": 1,
  "degraded": true
}
```"""

    commit = parse_stop_route_commit(message, "C:/work/repository", "session-1", "turn-1")

    assert commit == {
        "schema_version": "skilltree-route-commit/v1",
        "route_token": None,
        "workspace_id": _hash("C:/work/repository"),
        "session_id_hash": _hash("session-1"),
        "session_id": "session-1",
        "turn_id": "turn-1",
        "decision": {
            "schema_version": "skilltree/v1",
            "selected_skill_name": "analyze",
            "ordered_skill_names": ["analyze"],
            "degraded": True,
        },
    }


def test_stop_parser_preserves_non_degraded_visible_json_summary() -> None:
    message = """```json
{
  "selected_skill": "analyze",
  "ordered_skills": ["analyze"],
  "confidence": 0.95,
  "degraded": false
}
```"""

    commit = parse_stop_route_commit(message, "C:/work/repository", "session-1", "turn-1")

    assert commit is not None
    assert commit["decision"]["degraded"] is False


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
