"""SQLite-backed pending-memory lifecycle for P5."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from skilltree.core.evidence import EvidenceBundle, build_evidence_bundle
from skilltree.core.memory_candidates import MemoryCandidateSchemaError, normalize_memory_extraction_candidate
from skilltree.core.memory_extractors import CandidateLLM, ProfileExtractor, extract_memory_candidates
from skilltree.core.storage import Database


_TOKEN = re.compile(r"[\w]+", re.UNICODE)
_PENDING_TTL = timedelta(days=7)
_RETENTION_TTL = timedelta(days=30)
_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN = timedelta(seconds=60)
_BREAKER_POLICY = "memory-breaker/v1"
_INFRASTRUCTURE_FAILURES = {
    "sqlite_busy",
    "sqlite_io",
    "disk_full",
    "migration_error",
}


class MemoryStoreError(ValueError):
    """A stable error raised by the P5 candidate store."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def acquire_memory_write_slot(database: Database, *, workspace_id: str) -> bool:
    """Atomically admit a workspace write, including the single half-open probe."""
    try:
        with closing(database._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            _acquire_memory_write_slot(connection, workspace_id=workspace_id, now=_now())
            connection.commit()
    except MemoryStoreError:
        raise
    except sqlite3.Error as error:
        raise MemoryStoreError("internal_error") from error
    return True


def _acquire_memory_write_slot(
    connection: sqlite3.Connection, *, workspace_id: str, now: datetime
) -> None:
    now_text = _format_utc(now)
    row = connection.execute(
        "SELECT state, consecutive_failures, open_until FROM memory_write_breakers WHERE workspace_id=?",
        (workspace_id,),
    ).fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO memory_write_breakers(workspace_id,state,consecutive_failures,open_until,updated_at,retention_until) "
            "VALUES (?, 'closed', 0, NULL, ?, ?)",
            (workspace_id, now_text, _format_utc(now + _RETENTION_TTL)),
        )
        return
    if row[0] == "open":
        open_until = _parse_utc(row[2])
        if open_until > now:
            raise MemoryStoreError("memory_write_degraded")
        changed = connection.execute(
            "UPDATE memory_write_breakers SET state='half_open',open_until=NULL,updated_at=?,retention_until=? "
            "WHERE workspace_id=? AND state='open' AND open_until<=?",
            (now_text, _format_utc(now + _RETENTION_TTL), workspace_id, now_text),
        ).rowcount
        if changed != 1:
            raise MemoryStoreError("memory_write_degraded")
    elif row[0] == "half_open":
        raise MemoryStoreError("memory_write_degraded")


def record_memory_write_failure(
    database: Database, *, workspace_id: str, reason_code: str
) -> dict[str, object]:
    """Record only an allow-listed infrastructure failure and return breaker state."""
    if reason_code not in _INFRASTRUCTURE_FAILURES:
        return {"state": "unchanged", "counted": False}
    now = _now()
    now_text = _format_utc(now)
    opened = False
    try:
        with closing(database._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, consecutive_failures FROM memory_write_breakers WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
            failures = min(_BREAKER_THRESHOLD, int(row[1]) + 1) if row else 1
            state = "open" if failures >= _BREAKER_THRESHOLD else "closed"
            opened = state == "open" and (row is None or row[0] != "open")
            connection.execute(
                "INSERT INTO memory_write_breakers(workspace_id,state,consecutive_failures,open_until,updated_at,retention_until) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET state=excluded.state, "
                "consecutive_failures=excluded.consecutive_failures,open_until=excluded.open_until,"
                "updated_at=excluded.updated_at,retention_until=excluded.retention_until",
                (
                    workspace_id, state, failures,
                    _format_utc(now + _BREAKER_COOLDOWN) if state == "open" else None,
                    now_text, _format_utc(now + _RETENTION_TTL),
                ),
            )
            if opened:
                _best_effort_breaker_audit(connection, workspace_id, "memory_breaker_opened", reason_code, now)
            connection.commit()
    except sqlite3.Error:
        return {"state": "unknown", "counted": False}
    return {"state": state, "counted": True, "opened": opened}


def record_memory_write_success(database: Database, *, workspace_id: str) -> None:
    """Close a breaker after a successful durable write; audit is best effort."""
    try:
        with closing(database._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            _record_memory_write_success(connection, workspace_id=workspace_id, now=_now())
            connection.commit()
    except sqlite3.Error:
        return


def _record_memory_write_success(
    connection: sqlite3.Connection, *, workspace_id: str, now: datetime
) -> None:
    previous = connection.execute(
        "SELECT state FROM memory_write_breakers WHERE workspace_id=?", (workspace_id,)
    ).fetchone()
    if previous is None:
        return
    connection.execute(
        "UPDATE memory_write_breakers SET state='closed',consecutive_failures=0,open_until=NULL,"
        "updated_at=?,retention_until=? WHERE workspace_id=?",
        (_format_utc(now), _format_utc(now + _RETENTION_TTL), workspace_id),
    )
    if previous[0] in {"open", "half_open"}:
        _best_effort_breaker_audit(connection, workspace_id, "memory_breaker_recovered", "write_success", now)


def _best_effort_breaker_audit(
    connection: sqlite3.Connection, workspace_id: str, event_type: str, reason_code: str, now: datetime
) -> None:
    try:
        connection.execute(
            "INSERT INTO audit_events(audit_id,scope,workspace_id,event_type,object_handle_hash,reason_code,"
            "policy_version,created_at,retention_until) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()), "workspace", workspace_id, event_type, _hash(workspace_id), reason_code,
                _BREAKER_POLICY, _format_utc(now), _format_utc(now + _RETENTION_TTL),
            ),
        )
    except sqlite3.Error:
        return


def _sqlite_failure_reason(error: sqlite3.Error) -> str | None:
    message = str(error).casefold()
    if "locked" in message or "busy" in message:
        return "sqlite_busy"
    if "disk i/o" in message or "i/o error" in message:
        return "sqlite_io"
    if "full" in message:
        return "disk_full"
    if "no such table" in message or "schema" in message or "migration" in message:
        return "migration_error"
    return None


def _record_sqlite_failure(database: Database, workspace_id: str | None, error: sqlite3.Error) -> bool:
    reason = _sqlite_failure_reason(error)
    if workspace_id is None or reason is None:
        return False
    result = record_memory_write_failure(database, workspace_id=workspace_id, reason_code=reason)
    return result.get("state") == "open"


def extract_and_store_memory_candidates(
    database: Database, *, run_id: str, llm: CandidateLLM
) -> dict[str, object]:
    """Generate candidates from one eligible Run and keep them pending."""
    bundle = build_evidence_bundle(database, run_id=run_id)
    if bundle is None:
        raise MemoryStoreError("evidence_unavailable")
    candidates = extract_memory_candidates(bundle, llm=llm)
    created = [
        create_memory_candidate(database, run_id=run_id, candidate=candidate)
        for candidate in candidates
    ]
    return {"pending": len(created), "candidates": created}


def extract_and_store_profile_candidates(
    database: Database,
    *,
    user_id: str,
    workspace_id: str,
    durable_preference_statements: tuple[str, ...],
    transient_user_instructions: tuple[str, ...] = (),
    response_feedback: str = "none",
    llm: CandidateLLM,
) -> dict[str, object]:
    """Extract Profile candidates from explicit, non-persisted user evidence."""
    with closing(database._connect()) as connection:
        if not _memory_write_enabled(connection):
            raise MemoryStoreError("disabled")
    bundle = EvidenceBundle(
        schema_version="skilltree-evidence-bundle/v1",
        run_id="profile-evidence",
        workspace_id=workspace_id,
        user_id=user_id,
        task_type="profile_evidence",
        scenario_key="",
        scenario_label="",
        recommended_skills=(),
        observed_tool_steps=(),
        outcome="unknown",
        outcome_evidence_kind="none",
        durable_preference_statements=durable_preference_statements,
        transient_user_instructions=transient_user_instructions,
        response_feedback=response_feedback,
        evidence_event_ids=(),
        coverage_state="unobserved",
        route_degraded=False,
    )
    extraction = ProfileExtractor(llm=llm).extract(bundle)
    profiles = extraction.get("profile_fields")
    if not isinstance(profiles, list):
        raise MemoryStoreError("invalid_schema")
    created = [
        create_import_memory_candidate(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            candidate={
                "schema_version": "skilltree/v1",
                "profile_fields": [profile],
                "procedural_candidates": [],
            },
        )
        for profile in profiles
    ]
    return {"pending": len(created), "candidates": created}


def create_memory_candidate(
    database: Database, *, run_id: str, candidate: object
) -> dict[str, object]:
    """Persist exactly one validated, pending candidate from one authorized Run."""
    workspace_id: str | None = None
    try:
        with closing(database._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                context = _run_context(connection, run_id)
                workspace_id = str(context["workspace_id"])
                if not _memory_write_enabled(connection):
                    raise MemoryStoreError("disabled")
                _require_memory_write(context)
                _acquire_memory_write_slot(connection, workspace_id=workspace_id, now=_now())
                normalized = normalize_memory_extraction_candidate(
                    candidate,
                    available_skill_names=_run_skill_names(connection, run_id),
                    available_event_ids=_run_event_ids(connection, run_id),
                )
                layer, kind, payload = _one_payload(normalized)
                candidate_id = _insert_pending(
                    connection,
                    run_id=run_id,
                    workspace_id=context["workspace_id"],
                    user_id=context["user_id"],
                    layer=layer,
                    kind=kind,
                    payload=payload,
                )
                _record_memory_write_success(connection, workspace_id=workspace_id, now=_now())
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    except MemoryCandidateSchemaError:
        raise
    except MemoryStoreError:
        raise
    except sqlite3.Error as error:
        if _record_sqlite_failure(database, workspace_id, error):
            raise MemoryStoreError("memory_write_degraded") from error
        raise MemoryStoreError("internal_error") from error
    return {"candidate_id": candidate_id, "status": "pending", "layer": layer}


def list_memory_candidates(
    database: Database, *, user_id: str, workspace_id: str
) -> list[dict[str, object]]:
    """Return a bounded, owner-scoped view of live pending candidates."""
    try:
        with closing(database._connect()) as connection:
            if not _memory_write_enabled(connection):
                return []
            rows = connection.execute(
                "SELECT candidate_id,layer,kind,payload_json,created_at,expires_at FROM memory_candidates "
                "WHERE user_id=? AND workspace_id=? AND expires_at>? "
                "ORDER BY created_at,candidate_id LIMIT 50",
                (user_id, workspace_id, _utc_now()),
            ).fetchall()
    except sqlite3.Error as error:
        raise MemoryStoreError("internal_error") from error
    return [
        {
            "candidate_id": row[0], "layer": row[1], "kind": row[2],
            "payload": json.loads(row[3]), "created_at": row[4], "expires_at": row[5],
        }
        for row in rows
    ]


def create_import_memory_candidate(
    database: Database, *, user_id: str, workspace_id: str, candidate: object
) -> dict[str, object]:
    """Persist one explicit-import candidate without inventing a source Run."""
    try:
        with closing(database._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if not _memory_write_enabled(connection):
                    raise MemoryStoreError("disabled")
                _acquire_memory_write_slot(connection, workspace_id=workspace_id, now=_now())
                normalized = normalize_memory_extraction_candidate(
                    candidate, available_skill_names=set(), available_event_ids=set()
                )
                layer, kind, payload = _one_payload(normalized)
                candidate_id = _insert_pending(
                    connection, run_id=None, workspace_id=workspace_id, user_id=user_id,
                    layer=layer, kind=kind, payload=payload,
                )
                _record_memory_write_success(connection, workspace_id=workspace_id, now=_now())
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    except MemoryCandidateSchemaError:
        raise
    except MemoryStoreError:
        raise
    except sqlite3.Error as error:
        if _record_sqlite_failure(database, workspace_id, error):
            raise MemoryStoreError("memory_write_degraded") from error
        raise MemoryStoreError("internal_error") from error
    return {"candidate_id": candidate_id, "status": "pending", "layer": layer}


def approve_memory_candidate(
    database: Database, *, candidate_id: str, user_id: str, workspace_id: str
) -> dict[str, object]:
    """Approve one pending candidate and atomically create or update its memory."""
    try:
        with closing(database._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if not _memory_write_enabled(connection):
                    raise MemoryStoreError("disabled")
                row = connection.execute(
                    "SELECT run_id,workspace_id,user_id,layer,kind,payload_json,expires_at "
                    "FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
                ).fetchone()
                if row is None:
                    raise MemoryStoreError("not_found")
                run_id, owner_workspace, owner_user, layer, kind, payload_json, expires_at = row
                if owner_workspace != workspace_id or owner_user != user_id:
                    raise MemoryStoreError("out_of_scope")
                _acquire_memory_write_slot(connection, workspace_id=workspace_id, now=_now())
                now = _now()
                if _parse_utc(expires_at) <= now:
                    connection.execute("DELETE FROM memory_candidates WHERE candidate_id=?", (candidate_id,))
                    raise MemoryStoreError("conflict")
                payload = _load_payload(payload_json)
                if layer == "profile":
                    result = _approve_profile(
                        connection, candidate_id=candidate_id, run_id=run_id, user_id=user_id,
                        payload=payload, now=now,
                    )
                elif layer == "procedure":
                    result = _approve_procedure(
                        connection, candidate_id=candidate_id, run_id=run_id, workspace_id=workspace_id,
                        user_id=user_id, payload=payload, now=now,
                    )
                else:
                    raise MemoryStoreError("invalid_schema")
                connection.execute("DELETE FROM memory_candidates WHERE candidate_id=?", (candidate_id,))
                _record_memory_write_success(connection, workspace_id=workspace_id, now=_now())
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    except MemoryStoreError:
        raise
    except sqlite3.Error as error:
        if _record_sqlite_failure(database, workspace_id, error):
            raise MemoryStoreError("memory_write_degraded") from error
        raise MemoryStoreError("internal_error") from error
    return {"candidate_id": candidate_id, "layer": layer, **result, "completed_at": _format_utc(now)}


def reject_memory_candidate(
    database: Database, *, candidate_id: str, user_id: str, workspace_id: str
) -> dict[str, object]:
    """Delete one owner-scoped pending candidate without writing active memory."""
    try:
        with closing(database._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if not _memory_write_enabled(connection):
                    raise MemoryStoreError("disabled")
                row = connection.execute(
                    "SELECT workspace_id,user_id FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
                ).fetchone()
                if row is None:
                    raise MemoryStoreError("not_found")
                if row[0] != workspace_id or row[1] != user_id:
                    raise MemoryStoreError("out_of_scope")
                connection.execute("DELETE FROM memory_candidates WHERE candidate_id=?", (candidate_id,))
                completed_at = _utc_now()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    except MemoryStoreError:
        raise
    except sqlite3.Error as error:
        raise MemoryStoreError("internal_error") from error
    return {"candidate_id": candidate_id, "action": "rejected", "completed_at": completed_at}


def _run_context(connection: sqlite3.Connection, run_id: str) -> dict[str, str | int]:
    row = connection.execute(
        "SELECT workspace_id,user_id,memory_write_enabled FROM run_contexts WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise MemoryStoreError("not_found")
    if connection.execute("SELECT 1 FROM route_decisions WHERE run_id=?", (run_id,)).fetchone() is None:
        raise MemoryStoreError("out_of_scope")
    return {"workspace_id": row[0], "user_id": row[1], "memory_write_enabled": row[2]}


def _require_memory_write(context: dict[str, str | int]) -> None:
    if not bool(context["memory_write_enabled"]):
        raise MemoryStoreError("disabled")


def _memory_write_enabled(connection: sqlite3.Connection) -> bool:
    row = connection.execute("SELECT memory_write_enabled FROM runtime_config WHERE config_id=1").fetchone()
    return row is not None and bool(row[0])


def _run_skill_names(connection: sqlite3.Connection, run_id: str) -> set[str]:
    row = connection.execute("SELECT decision_json FROM route_decisions WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return set()
    try:
        decision = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return set()
    ordered = decision.get("ordered_skill_names") if isinstance(decision, dict) else None
    return {name for name in ordered if isinstance(name, str)} if isinstance(ordered, list) else set()


def _run_event_ids(connection: sqlite3.Connection, run_id: str) -> set[str]:
    return {row[0] for row in connection.execute("SELECT event_id FROM trace_events WHERE run_id=?", (run_id,))}


def _one_payload(candidate: dict[str, object]) -> tuple[str, str, dict[str, object]]:
    profiles = candidate["profile_fields"]
    procedures = candidate["procedural_candidates"]
    if not isinstance(profiles, list) or not isinstance(procedures, list) or len(profiles) + len(procedures) != 1:
        raise MemoryStoreError("invalid_schema")
    if profiles:
        payload = profiles[0]
        if not isinstance(payload, dict):
            raise MemoryStoreError("invalid_schema")
        return "profile", str(payload.get("namespace", "")), payload
    payload = procedures[0]
    if not isinstance(payload, dict):
        raise MemoryStoreError("invalid_schema")
    return "procedure", "procedure", payload


def _insert_pending(
    connection: sqlite3.Connection, *, run_id: str | None, workspace_id: str, user_id: str,
    layer: str, kind: str, payload: dict[str, object],
) -> str:
    now = _now()
    candidate_id = str(uuid4())
    serialized = _canonical_json(payload)
    connection.execute(
        "INSERT INTO memory_candidates(candidate_id,run_id,workspace_id,user_id,layer,kind,scope,payload_json,"
        "payload_hash,status,created_at,expires_at,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            candidate_id, run_id, workspace_id, user_id, layer, kind,
            "user_global" if layer == "profile" else "workspace", serialized, _hash(serialized), "pending",
            _format_utc(now), _format_utc(now + _PENDING_TTL), _format_utc(now + _RETENTION_TTL),
        ),
    )
    return candidate_id


def _approve_profile(
    connection: sqlite3.Connection, *, candidate_id: str, run_id: str | None, user_id: str,
    payload: dict[str, object], now: datetime,
) -> dict[str, str]:
    namespace, key, value = payload["namespace"], payload["key"], payload["value"]
    if not all(isinstance(item, str) for item in (namespace, key, value)):
        raise MemoryStoreError("invalid_schema")
    existing = connection.execute(
        "SELECT profile_field_id,value FROM profile_fields WHERE user_id=? AND namespace=? AND field_key=?",
        (user_id, namespace, key),
    ).fetchone()
    if existing is not None and _normal_text(existing[1]) == _normal_text(value):
        return {"action": "no_op"}
    action = "updated" if existing is not None else "created"
    if existing is None:
        connection.execute(
            "INSERT INTO profile_fields(profile_field_id,user_id,scope,namespace,field_key,value,value_hash,"
            "source_candidate_id,source_run_id,created_at,updated_at,retention_until) VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (str(uuid4()), user_id, "user_global", namespace, key, value, _hash(_normal_text(value)),
             candidate_id, run_id, _format_utc(now), _format_utc(now)),
        )
    else:
        connection.execute(
            "UPDATE profile_fields SET value=?,value_hash=?,source_candidate_id=?,source_run_id=?,updated_at=? "
            "WHERE profile_field_id=?",
            (value, _hash(_normal_text(value)), candidate_id, run_id, _format_utc(now), existing[0]),
        )
    return {"action": action}


def _approve_procedure(
    connection: sqlite3.Connection, *, candidate_id: str, run_id: str | None, workspace_id: str,
    user_id: str, payload: dict[str, object], now: datetime,
) -> dict[str, str]:
    rule = payload.get("rule")
    applies_to = payload.get("applies_to")
    scenario_key = payload.get("scenario_key", "")
    if not all(isinstance(value, str) for value in (rule, applies_to, scenario_key)):
        raise MemoryStoreError("invalid_schema")
    rule_hash = _hash(_normal_text(rule))
    existing = connection.execute(
        "SELECT procedure_id,reinforcement_count,importance_prior FROM procedures "
        "WHERE workspace_id=? AND user_id=? AND applies_to=? AND scenario_key=? AND rule_hash=?",
        (workspace_id, user_id, applies_to, scenario_key, rule_hash),
    ).fetchone()
    if existing is None:
        existing = _near_duplicate(connection, workspace_id, user_id, applies_to, scenario_key, rule)
    if existing is not None:
        procedure_id, reinforcement_count, importance = existing
        count = int(reinforcement_count) + 1
        score = _score(float(importance), count, now, now)
        strength = "strong" if score >= 70.0 and count >= 2 else "weak"
        expires = now + _procedure_ttl(strength)
        connection.execute(
            "UPDATE procedures SET reinforcement_count=?,seen_count=seen_count+1,last_reinforced_at=?,"
            "score=?,strength=?,status='active',hidden_at=NULL,expires_at=?,updated_at=?,source_candidate_id=?,source_run_id=? "
            "WHERE procedure_id=?",
            (count, _format_utc(now), score, strength, _format_utc(expires), _format_utc(now),
             candidate_id, run_id, procedure_id),
        )
        return {"action": "reinforced"}

    importance = payload.get("importance_prior", 0.5)
    if isinstance(importance, bool) or not isinstance(importance, (int, float)):
        raise MemoryStoreError("invalid_schema")
    importance = float(importance)
    strength = "weak"
    connection.execute(
        "INSERT INTO procedures(procedure_id,workspace_id,user_id,scope,rule,rule_hash,shingle_fingerprint,"
        "applies_to,scenario_key,scenario_label,recommended_skill_names_json,ordering_constraints_json,avoid_when,"
        "strength,importance_prior,reinforcement_count,seen_count,usage_score,recency_score,score,low_score_sweeps,"
        "last_reinforced_at,status,source_candidate_id,source_run_id,created_at,updated_at,expires_at,hidden_at,retention_until) "
        "VALUES (" + ",".join("?" for _ in range(30)) + ")",
        (
            str(uuid4()), workspace_id, user_id, "workspace", rule, rule_hash, _hash(_fingerprint(rule)),
            applies_to, scenario_key, payload.get("scenario_label", ""),
            _canonical_json(payload.get("recommended_skill_names", [])),
            _canonical_json(payload.get("ordering_constraints", [])), payload.get("avoid_when", ""),
            strength, importance, 0, 1, 0.0, 1.0, _score(importance, 0, now, now), 0,
            _format_utc(now), "active", candidate_id, run_id, _format_utc(now), _format_utc(now),
            _format_utc(now + _procedure_ttl(strength)), None, _format_utc(now + _procedure_ttl(strength) + _RETENTION_TTL),
        ),
    )
    return {"action": "created"}


def _near_duplicate(
    connection: sqlite3.Connection, workspace_id: str, user_id: str, applies_to: str,
    scenario_key: str, rule: str,
) -> tuple[str, int, float] | None:
    tokens = _rule_tokens(rule)
    rows = connection.execute(
        "SELECT procedure_id,rule,reinforcement_count,importance_prior FROM procedures "
        "WHERE workspace_id=? AND user_id=? AND applies_to=? AND scenario_key=?",
        (workspace_id, user_id, applies_to, scenario_key),
    ).fetchall()
    for procedure_id, existing_rule, count, importance in rows:
        other = _rule_tokens(existing_rule)
        union = tokens | other
        if union and len(tokens & other) / len(union) >= 0.60:
            return procedure_id, count, importance
    return None


def _load_payload(value: str) -> dict[str, object]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise MemoryStoreError("invalid_schema") from error
    if not isinstance(payload, dict):
        raise MemoryStoreError("invalid_schema")
    return payload


def _normal_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _rule_tokens(value: str) -> set[str]:
    return set(_TOKEN.findall(_normal_text(value)))


def _fingerprint(value: str) -> str:
    return " ".join(sorted(_rule_tokens(value)))


def _score(importance_prior: float, reinforcement_count: int, last_reinforced_at: datetime, now: datetime) -> float:
    days = max(0.0, (now - last_reinforced_at).total_seconds() / 86400)
    usage = 1 - math.exp(-reinforcement_count / 3)
    recency = math.exp(-days * math.log(2) / 70)
    return 100 * (0.40 * importance_prior + 0.35 * usage + 0.25 * recency)


def _procedure_ttl(strength: str) -> timedelta:
    return timedelta(days=365 if strength == "strong" else 30)


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> datetime:
    return datetime.now(UTC)


def _utc_now() -> str:
    return _format_utc(_now())


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
