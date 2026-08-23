"""Host-format-independent P2 bridge between Codex Hooks and the routing CLI."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from skilltree.core.bundle import BundleValidationError, validate_bundle
from skilltree.core.doctor import diagnose
from skilltree.core.outbox import AtomicOutbox
from skilltree.core.trace_events import build_trace_event
from skilltree.registry_service.registry import REGISTRY_CAPACITY


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PUBLIC_REF = re.compile(r"ref:[0-9a-f]{8,12}\Z")
_ROUTE_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_ROUTE_MARKER = "<!-- skilltree-route-decision:"
_ROUTE_COMMENT = re.compile(r"<!-- skilltree-route-decision:(\{.*\}) -->\Z", re.DOTALL)
_VISIBLE_SUMMARY = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_HOOK_EVENTS = {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
_PROBE_LOCK = Lock()
_COMMITTED_ROUTE_KEYS: set[tuple[str, str, str]] = set()


@dataclass(frozen=True)
class HookInput:
    """Validated, host-neutral Hook fields kept in memory for one invocation."""

    event_name: str
    fields: dict[str, object]


def prepare_user_prompt_context(
    payload: object,
    doctor: object,
    prepare_route: Callable[[str, str, str], dict[str, object]],
) -> dict[str, object] | None:
    """Prepare a safe RouteEnvelope, or fail open without emitting context."""
    if not isinstance(doctor, dict) or doctor.get("diagnostic_state") not in {"ready", "degraded"}:
        return None
    prompt, session_id, cwd = _prompt_identifiers(payload)
    if prompt is None or session_id is None or cwd is None:
        return None
    try:
        envelope = prepare_route(_hash(cwd), _hash(session_id), prompt)
    except Exception:
        return None
    if not _valid_internal_route_envelope(envelope):
        return None
    return project_route_envelope(envelope)


def parse_stop_route_commit(message: object, cwd: object, session_id: object, turn_id: object = None) -> dict[str, object] | None:
    """Extract exactly one final P2 decision comment without retaining the message."""
    if not isinstance(message, str) or not isinstance(cwd, str) or not isinstance(session_id, str):
        return None
    if not _valid_opaque_identifier(cwd, 4096) or not _valid_opaque_identifier(session_id, 128):
        return None
    if turn_id is not None and not _valid_opaque_identifier(turn_id, 256):
        return None
    marker_count = message.count(_ROUTE_MARKER)
    if marker_count > 1:
        return None
    if marker_count == 1:
        final_lines = [line for line in message.splitlines() if line.strip()]
        if not final_lines:
            return None
        comment = final_lines[-1].strip()
        if len(comment.encode("utf-8")) > 4096:
            return None
        decoded = _decode_route_comment(comment)
    else:
        decoded = _decode_visible_summary(message)
    if decoded is None:
        return None
    result = {
        "schema_version": "skilltree-route-commit/v1",
        "route_token": decoded.get("route_token"),
        "workspace_id": _hash(cwd),
        "session_id_hash": _hash(session_id),
        "decision": decoded["decision"],
    }
    if result["route_token"] is None:
        if not isinstance(turn_id, str):
            return None
        result["session_id"] = session_id
        result["turn_id"] = turn_id
    return result


def parse_hook_input(event_name: str, raw: bytes) -> HookInput:
    """Parse one bounded host-neutral event without retaining unknown fields."""
    if event_name not in _HOOK_EVENTS or not isinstance(raw, bytes) or len(raw) > 32 * 1024:
        raise ValueError("invalid_hook_input")
    try:
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RouteCommentError):
        raise ValueError("invalid_hook_input") from None
    if not isinstance(decoded, dict):
        raise ValueError("invalid_hook_input")
    required: dict[str, tuple[str, ...]] = {
        "UserPromptSubmit": ("turn_id", "session_id", "cwd", "prompt"),
        "PreToolUse": ("turn_id", "session_id", "cwd", "tool_name", "tool_use_id", "tool_input"),
        "PostToolUse": ("turn_id", "session_id", "cwd", "tool_name", "tool_use_id", "tool_response"),
        "Stop": ("session_id", "cwd", "last_assistant_message"),
    }
    if any(key not in decoded for key in required[event_name]):
        raise ValueError("invalid_hook_input")
    _validate_opaque(decoded.get("session_id"), 256)
    _validate_opaque(decoded.get("cwd"), 4096)
    if event_name != "Stop":
        _validate_opaque(decoded.get("turn_id"), 256)
    elif "turn_id" in decoded:
        _validate_opaque(decoded.get("turn_id"), 256)
    if event_name == "UserPromptSubmit":
        prompt = decoded.get("prompt")
        if not isinstance(prompt, str) or not prompt or len(prompt.encode("utf-8")) > 16 * 1024:
            raise ValueError("invalid_hook_input")
    if event_name == "Stop" and decoded.get("last_assistant_message") is not None and not isinstance(decoded.get("last_assistant_message"), str):
        raise ValueError("invalid_hook_input")
    fields = {key: decoded[key] for key in required[event_name]}
    if event_name == "Stop" and "turn_id" in decoded:
        fields["turn_id"] = decoded["turn_id"]
    return HookInput(event_name, fields)


def extract_route_decision(last_assistant_message: str) -> dict[str, object] | None:
    """Return a single final-line decision comment without workspace context."""
    if not isinstance(last_assistant_message, str):
        return None
    if last_assistant_message.count(_ROUTE_MARKER) == 0:
        return _decode_visible_summary(last_assistant_message)
    if last_assistant_message.count(_ROUTE_MARKER) != 1:
        return None
    lines = [line for line in last_assistant_message.splitlines() if line.strip()]
    if not lines or len(lines[-1].strip().encode("utf-8")) > 4096:
        return None
    decoded = _decode_route_comment(lines[-1].strip())
    return decoded


def _decode_visible_summary(message: str) -> dict[str, object] | None:
    """Accept only the bounded JSON summary left when a host strips HTML comments."""
    matches = _VISIBLE_SUMMARY.findall(message)
    if len(matches) != 1 or len(matches[0].encode("utf-8")) > 2048:
        return None
    try:
        summary = json.loads(matches[0], object_pairs_hook=_object_without_duplicate_keys)
    except (json.JSONDecodeError, RouteCommentError):
        return None
    if not isinstance(summary, dict) or set(summary) != {"selected_skill", "ordered_skills", "confidence", "degraded"}:
        return None
    selected = summary["selected_skill"]
    ordered = summary["ordered_skills"]
    confidence = summary["confidence"]
    if (
        not isinstance(selected, str)
        or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", selected) is None
        or not isinstance(ordered, list)
        or not 1 <= len(ordered) <= 3
        or any(not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) is None for name in ordered)
        or len(set(ordered)) != len(ordered)
        or ordered[0] != selected
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0.0 <= confidence <= 1.0
        or type(summary["degraded"]) is not bool
    ):
        return None
    return {
        "schema_version": "skilltree-route-commit/v1",
        "decision": {
            "schema_version": "skilltree/v1",
            "selected_skill_name": selected,
            "ordered_skill_names": ordered,
            "degraded": summary["degraded"],
        },
    }


def build_probe(
    event: HookInput,
    hook_bundle_hash: str,
    now: datetime,
    *,
    code: str = "observed",
) -> dict[str, object]:
    """Build a bounded shape-only diagnostic record for a Hook event."""
    if event.event_name not in {"PreToolUse", "PostToolUse", "Stop"}:
        raise ValueError("probe_event_invalid")
    if not _SHA256.fullmatch(hook_bundle_hash):
        raise ValueError("hook_bundle_hash_invalid")
    if code not in {"observed", "invalid_input", "decision_missing", "commit_error", "trace_unattributed"}:
        raise ValueError("probe_code_invalid")
    shapes = {
        key: {"json_type": _json_type(value), "length_bucket": _length_bucket(value)}
        for key, value in event.fields.items()
    }
    canonical = _canonical_json({"event_name": event.event_name, "field_shapes": shapes, "hook_bundle_hash": hook_bundle_hash})
    return {
        "schema_version": "skilltree-hook-probe/v1",
        "observation_id": str(uuid4()),
        "event_name": event.event_name,
        "field_shapes": shapes,
        "hook_bundle_hash": hook_bundle_hash,
        "shape_hash": _hash(canonical),
        "code": code,
        "observed_at": _format_utc(now),
    }


def build_raw_shape_probe(
    event_name: str,
    raw: bytes,
    hook_bundle_hash: str,
    now: datetime,
    *,
    code: str,
) -> dict[str, object]:
    """Build a shape-only Probe when the Stop payload cannot be parsed."""
    if event_name != "Stop" or not isinstance(raw, bytes):
        raise ValueError("probe_event_invalid")
    if not _SHA256.fullmatch(hook_bundle_hash):
        raise ValueError("hook_bundle_hash_invalid")
    if code != "invalid_input":
        raise ValueError("probe_code_invalid")
    shapes: dict[str, dict[str, str]] = {
        "stdin": {"json_type": "bytes", "length_bucket": _length_bucket(raw)},
    }
    try:
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RouteCommentError):
        decoded = None
    if isinstance(decoded, dict):
        shapes = {
            str(key): {"json_type": _json_type(value), "length_bucket": _length_bucket(value)}
            for key, value in decoded.items()
        }
    canonical = _canonical_json({"event_name": event_name, "field_shapes": shapes, "hook_bundle_hash": hook_bundle_hash})
    return {
        "schema_version": "skilltree-hook-probe/v1",
        "observation_id": str(uuid4()),
        "event_name": event_name,
        "field_shapes": shapes,
        "hook_bundle_hash": hook_bundle_hash,
        "shape_hash": _hash(canonical),
        "code": code,
        "observed_at": _format_utc(now),
    }


def write_probe(data_dir: Path, probe: dict[str, object], now: datetime) -> None:
    """Atomically write a bounded Probe and apply the best-effort retention cap."""
    if not isinstance(probe, dict):
        raise ValueError("probe_invalid")
    payload = _redact_public_hashes(dict(probe))
    payload.setdefault("observation_id", str(uuid4()))
    serialized = _canonical_json(payload).encode("utf-8")
    if len(serialized) > 4 * 1024:
        raise ValueError("probe_too_large")
    directory = data_dir / "diagnostics" / "g0.25"
    with _PROBE_LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        _cleanup_probes(directory, now)
        files = sorted(directory.glob("*.json"), key=_probe_mtime)
        while len(files) >= 100:
            victim = files.pop(0)
            try:
                victim.unlink()
            except OSError:
                pass
        observation_id = str(payload["observation_id"])
        temp_path = directory / f".{observation_id}.{uuid4().hex}.tmp"
        final_path = directory / f"{observation_id}.json"
        try:
            with open(temp_path, "xb") as handle:
                handle.write(serialized)
            os.replace(temp_path, final_path)
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass


def handle_hook_event(
    event_name: str,
    stdin_bytes: bytes,
    *,
    data_dir: Path,
    plugin_root: Path,
    database: object,
    now: datetime | None = None,
) -> tuple[int, str]:
    """Execute one consent-gated bridge call; every failure degrades to empty stdout."""
    try:
        if event_name not in _HOOK_EVENTS or not bool(database.read_runtime_settings().get("trace_capture_enabled")):
            return 0, ""
        doctor_result, _ = diagnose(data_dir)
        if doctor_result.get("diagnostic_state") not in {"ready", "degraded"}:
            return 0, ""
        current = now or datetime.now(UTC)
        hook_hash = doctor_result.get("current_hook_bundle_hash")
        if not isinstance(hook_hash, str):
            hook_hash = validate_bundle(plugin_root)["hook_bundle"]["hash"]
        if event_name == "Stop":
            try:
                event = parse_hook_input(event_name, stdin_bytes)
            except Exception:
                try:
                    write_probe(data_dir, build_raw_shape_probe(event_name, stdin_bytes, hook_hash, current, code="invalid_input"), current)
                except Exception:
                    pass
                return 0, ""
        else:
            event = parse_hook_input(event_name, stdin_bytes)
        _enqueue_trace_observation(data_dir, database, event, current, hook_hash)
        if event_name == "UserPromptSubmit":
            workspace_id = _hash(str(event.fields["cwd"]))
            session_id = str(event.fields["session_id"])
            session_hash = _hash(session_id)
            prompt = str(event.fields["prompt"])
            envelope = prepare_user_prompt_context(
                event.fields, doctor_result,
                database.prepare_route,
            )
            if envelope is None:
                return 0, ""
            reserved = database.trace_reserve(
                workspace_id=workspace_id,
                session_id=session_id,
                session_id_hash=session_hash,
                turn_id=str(event.fields["turn_id"]),
                prompt_hash=_hash(prompt),
                route_token=envelope["route_token"],
            )
            if not isinstance(reserved, dict) or not reserved.get("turn_trace_id"):
                return 0, ""
            context = _canonical_json(envelope)
            return 0, _canonical_json({
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": context,
                }
            })
        if event_name == "Stop":
            commit = parse_stop_route_commit(
                event.fields["last_assistant_message"], event.fields["cwd"], event.fields["session_id"], event.fields.get("turn_id")
            )
            if commit is None:
                try:
                    write_probe(data_dir, build_probe(event, hook_hash, current, code="decision_missing"), current)
                except Exception:
                    pass
                return 0, ""
            key = (str(commit.get("route_token") or commit.get("turn_id")), str(commit["workspace_id"]), str(commit["session_id_hash"]))
            with _PROBE_LOCK:
                already_committed = key in _COMMITTED_ROUTE_KEYS
            if already_committed:
                try:
                    write_probe(data_dir, build_probe(event, hook_hash, current), current)
                except Exception:
                    pass
                return 0, ""
            try:
                if commit.get("route_token") is None:
                    database.commit_current_route(commit["workspace_id"], str(commit["session_id"]), str(commit["turn_id"]), commit["decision"])
                else:
                    database.commit_route(commit["route_token"], commit["workspace_id"], commit["session_id_hash"], commit["decision"])
            except Exception:
                try:
                    write_probe(data_dir, build_probe(event, hook_hash, current, code="commit_error"), current)
                except Exception:
                    pass
                return 0, ""
            try:
                write_probe(data_dir, build_probe(event, hook_hash, current), current)
            except Exception:
                pass
            with _PROBE_LOCK:
                _COMMITTED_ROUTE_KEYS.add(key)
                if len(_COMMITTED_ROUTE_KEYS) > 1000:
                    _COMMITTED_ROUTE_KEYS.pop()
            return 0, ""
        write_probe(data_dir, build_probe(event, hook_hash, current), current)
        return 0, ""
    except Exception:
        return 0, ""


class RouteCommentError(ValueError):
    """Internal duplicate-key marker for untrusted model comments."""


def _prompt_identifiers(payload: object) -> tuple[str | None, str | None, str | None]:
    if not isinstance(payload, dict):
        return None, None, None
    prompt = payload.get("prompt")
    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    if (
        not isinstance(prompt, str)
        or not prompt
        or len(prompt.encode("utf-8")) > 16 * 1024
        or not _valid_opaque_identifier(session_id, 128)
        or not _valid_opaque_identifier(cwd, 4096)
    ):
        return None, None, None
    return prompt, session_id, cwd


def _enqueue_trace_observation(data_dir: Path, database: object, event: HookInput, now: datetime, hook_hash: str) -> None:
    """Queue a sanitized Pre/Post observation; SQLite remains single-writer owned."""
    if event.event_name not in {"PreToolUse", "PostToolUse", "Stop"}:
        return
    workspace_id = _hash(str(event.fields["cwd"]))
    session_id = str(event.fields["session_id"])
    turn_id_value = event.fields.get("turn_id")
    turn_id = str(turn_id_value) if isinstance(turn_id_value, str) else None
    resolver = getattr(database, "find_turn_trace", None)
    resolved = resolver(workspace_id, session_id, turn_id) if callable(resolver) else None
    if not isinstance(resolved, dict) or not resolved.get("turn_trace_id"):
        return
    event_type = "tool_started" if event.event_name == "PreToolUse" else "tool_finished" if event.event_name == "PostToolUse" else "run_closed"
    summary = summarize_tool_operation(event)
    payload_hash = _hash(_canonical_json({"event_name": event.event_name, "tool_name": event.fields.get("tool_name")}))
    payload = build_trace_event(
        event_id=str(uuid4()),
        turn_trace_id=str(resolved["turn_trace_id"]),
        run_id=str(resolved.get("run_id") or "") or None,
        event_type=event_type,
        source=f"hook:{hook_hash}",
        coverage_state="partial" if resolved.get("correlation") == "fallback" else "observed",
        observed_at=_format_utc(now),
        payload_summary=summary,
        payload_hash=payload_hash,
        tool_use_id=str(event.fields["tool_use_id"]) if event.event_name != "Stop" else None,
        tool_name=str(event.fields["tool_name"]) if event.event_name != "Stop" else None,
    )
    try:
        AtomicOutbox(data_dir / "outbox").enqueue_trace_event(payload)
    except Exception:
        return


def summarize_tool_operation(event: HookInput) -> str:
    """Return a fixed operation-category summary without retaining Tool contents."""
    tool_name = str(event.fields.get("tool_name", ""))
    prefix = f"{event.event_name}:{tool_name}"
    if event.event_name != "PreToolUse":
        return prefix[:256]
    tool_input = event.fields.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    direct_categories = {
        "read_file": "read_source",
        "read_source": "read_source",
        "list_files": "inspect_files",
        "ast-grep": "ast_search",
        "sg": "ast_search",
        "lsp": "language_service",
        "language_server": "language_service",
        "pytest": "run_tests",
        "python": "run_python",
    }
    categories: list[str] = []
    direct_category = direct_categories.get(tool_name.casefold())
    if direct_category is not None:
        categories.append(direct_category)
    if not isinstance(command, str):
        return f"{prefix}:{','.join(categories)}"[:256] if categories else prefix[:256]
    checks = (
        ("inspect_files", r"(?i)(?:^|[;&|]\s*)(?:get-childitem|dir|ls)\b"),
        ("read_source", r"(?i)(?:^|[;&|]\s*)(?:get-content|type|cat|sed)\b"),
        ("search_source", r"(?i)(?:^|[;&|]\s*)(?:rg|ripgrep|findstr|select-string)\b"),
        ("ast_search", r"(?i)(?:^|[;&|]\s*)(?:sg|ast-grep)\b"),
        # Match the bounded operation name even when PowerShell invokes an
        # absolute interpreter path through its call operator.
        ("run_tests", r"(?i)(?:pytest\b|python(?:3)?(?:\.exe)?\s+-m\s+pytest\b|py(?:\.exe)?\s+-\d+\s+-m\s+pytest\b|\$(?:py|python)\s+-m\s+pytest\b)"),
        ("language_service", r"(?i)(?:\bpyright\b|\bpylance\b|\bmypy\b|\bruff\s+check\b)"),
        ("run_python", r"(?i)(?:\bpython(?:3)?(?:\.exe)?\b(?!\s+-m\s+pytest)|\bpy(?:\.exe)?\s+-\d+\b(?!\s+-m\s+pytest)|\$(?:py|python)\s+-c\b)"),
        ("visualization", r"(?i)(?:\bmatplotlib\b|\bseaborn\b|\bplotly\b)"),
    )
    for category, pattern in checks:
        if re.search(pattern, command):
            categories.append(category)
    return f"{prefix}:{','.join(categories)}"[:256] if categories else prefix[:256]


def public_hash_ref(value: str) -> str:
    """Return a non-authoritative short reference for a complete SHA-256 value."""
    if _SHA256.fullmatch(value) is None:
        raise ValueError("hash_invalid")
    return "ref:" + value[7:19]


def project_route_envelope(envelope: dict[str, object]) -> dict[str, object]:
    """Project an internal envelope without exposing complete content hashes."""
    if not _valid_internal_route_envelope(envelope):
        raise ValueError("route_envelope_invalid")
    candidates = envelope["candidates"]
    assert isinstance(candidates, list)
    projected = {
        **envelope,
        "candidate_snapshot_hash": public_hash_ref(str(envelope["candidate_snapshot_hash"])),
        "candidates": [
            {**candidate, "content_hash": public_hash_ref(str(candidate["content_hash"]))}
            for candidate in candidates
        ],
    }
    if not validate_public_route_envelope(projected):
        raise ValueError("public_route_envelope_invalid")
    return projected


def validate_internal_route_envelope(value: object) -> bool:
    return _valid_internal_route_envelope(value)


def validate_public_route_envelope(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "route_token", "expires_at", "candidate_snapshot_hash", "degraded", "candidates"
    }:
        return False
    if (
        value["schema_version"] != "skilltree-route-envelope/v1"
        or not isinstance(value["route_token"], str)
        or _ROUTE_TOKEN.fullmatch(value["route_token"]) is None
        or not isinstance(value["expires_at"], str)
        or _PUBLIC_REF.fullmatch(value["candidate_snapshot_hash"]) is None
        or not isinstance(value["degraded"], bool)
        or not isinstance(value["candidates"], list)
        or not 1 <= len(value["candidates"]) <= REGISTRY_CAPACITY
    ):
        return False
    names: list[str] = []
    for candidate in value["candidates"]:
        if not isinstance(candidate, dict) or set(candidate) != {"name", "description", "content_hash"}:
            return False
        name, description, content_hash = candidate["name"], candidate["description"], candidate["content_hash"]
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(description, str)
            or not description
            or len(description) > 500
            or not isinstance(content_hash, str)
            or _PUBLIC_REF.fullmatch(content_hash) is None
        ):
            return False
        names.append(name)
    return len(set(names)) == len(names)


def _valid_internal_route_envelope(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "route_token", "expires_at", "candidate_snapshot_hash", "degraded", "candidates"
    }:
        return False
    if (
        value["schema_version"] != "skilltree-route-envelope/v1"
        or not isinstance(value["route_token"], str)
        or _ROUTE_TOKEN.fullmatch(value["route_token"]) is None
        or not isinstance(value["expires_at"], str)
        or not _SHA256.fullmatch(value["candidate_snapshot_hash"])
        or not isinstance(value["degraded"], bool)
        or not isinstance(value["candidates"], list)
        or not 1 <= len(value["candidates"]) <= REGISTRY_CAPACITY
    ):
        return False
    names: list[str] = []
    for candidate in value["candidates"]:
        if not isinstance(candidate, dict) or set(candidate) != {"name", "description", "content_hash"}:
            return False
        name, description, content_hash = candidate["name"], candidate["description"], candidate["content_hash"]
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(description, str)
            or not description
            or len(description) > 500
            or not isinstance(content_hash, str)
            or _SHA256.fullmatch(content_hash) is None
        ):
            return False
        names.append(name)
    return len(set(names)) == len(names)


def _redact_public_hashes(value: object) -> object:
    """Redact known hash fields at the diagnostic/Probe output boundary only."""
    hash_keys = {
        "hook_bundle_hash", "shape_hash", "bundle_hash", "candidate_snapshot_hash",
        "content_hash", "session_id_hash", "workspace_id",
    }
    if isinstance(value, dict):
        return {
            key: public_hash_ref(item) if key in hash_keys and isinstance(item, str) and _SHA256.fullmatch(item) else _redact_public_hashes(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_public_hashes(item) for item in value]
    return value


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RouteCommentError()
        result[key] = value
    return result


def _decode_route_comment(comment: str) -> dict[str, object] | None:
    match = _ROUTE_COMMENT.fullmatch(comment)
    if match is None:
        return None
    try:
        decoded = json.loads(match.group(1), object_pairs_hook=_object_without_duplicate_keys)
    except (RouteCommentError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict) or set(decoded) not in ({"schema_version", "route_token", "decision"}, {"schema_version", "decision"}):
        return None
    if decoded["schema_version"] != "skilltree-route-commit/v1" or not isinstance(decoded["decision"], dict):
        return None
    if "route_token" in decoded and (not isinstance(decoded["route_token"], str) or _ROUTE_TOKEN.fullmatch(decoded["route_token"]) is None):
        return None
    return decoded


def _validate_opaque(value: object, limit: int) -> None:
    if not _valid_opaque_identifier(value, limit):
        raise ValueError("invalid_hook_input")


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _length_bucket(value: object) -> str:
    if isinstance(value, (str, bytes)):
        length = len(value.encode("utf-8") if isinstance(value, str) else value)
    elif isinstance(value, (dict, list)):
        length = len(value)
    else:
        return "na"
    if length <= 0:
        return "0"
    if length <= 16:
        return "1-16"
    if length <= 256:
        return "17-256"
    if length <= 4096:
        return "257-4096"
    return ">4096"


def _cleanup_probes(directory: Path, now: datetime) -> None:
    cutoff = now.timestamp() - timedelta(hours=24).total_seconds()
    for path in directory.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _probe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _valid_opaque_identifier(value: object, limit: int) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= limit and "\x00" not in value


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
