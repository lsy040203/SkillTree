"""Deterministic, explicit P5 memory lifecycle maintenance."""

from __future__ import annotations

import sqlite3
import json
import math
from contextlib import closing
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from skilltree.core.storage import Database


_POLICY = "memory-lifecycle/v1"
_AUDIT_RETENTION = timedelta(days=30)
_HIDDEN_RETENTION = timedelta(days=30)
_LIMIT = 100


def sweep_memory_lifecycle(database: Database, *, now: datetime | None = None) -> dict[str, int]:
    """Run one bounded, idempotent lifecycle sweep."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    result = {
        "candidates_expired": 0,
        "procedures_hidden": 0,
        "procedures_purged": 0,
        "procedures_recomputed": 0,
        "audits_purged": 0,
    }
    with closing(database._connect()) as connection:
        candidate_ids = [row[0] for row in connection.execute(
            "SELECT candidate_id FROM memory_candidates WHERE status='pending' AND expires_at<=? "
            "ORDER BY expires_at,candidate_id LIMIT ?",
            (_format(current), _LIMIT),
        ).fetchall()]
        procedure_ids = [row[0] for row in connection.execute(
            "SELECT procedure_id FROM procedures WHERE status='active' AND expires_at<=? "
            "ORDER BY expires_at,procedure_id LIMIT ?",
            (_format(current), _LIMIT),
        ).fetchall()]
        active_ids = [row[0] for row in connection.execute(
            "SELECT procedure_id FROM procedures WHERE status='active' "
            "ORDER BY procedure_id LIMIT ?", (_LIMIT,),
        ).fetchall()]
        hidden_ids = [row[0] for row in connection.execute(
            "SELECT procedure_id FROM procedures WHERE status='hidden' AND retention_until<=? "
            "ORDER BY retention_until,procedure_id LIMIT ?",
            (_format(current), _LIMIT),
        ).fetchall()]
        audit_ids = [row[0] for row in connection.execute(
            "SELECT audit_id FROM audit_events WHERE retention_until<=? "
            "ORDER BY retention_until,audit_id LIMIT ?",
            (_format(current), _LIMIT),
        ).fetchall()]

    for candidate_id in candidate_ids:
        if _expire_candidate(database, candidate_id, current):
            result["candidates_expired"] += 1
    for procedure_id in procedure_ids:
        if _hide_procedure(database, procedure_id, current):
            result["procedures_hidden"] += 1
    for procedure_id in active_ids:
        if _recalculate_procedure(database, procedure_id, current):
            result["procedures_recomputed"] += 1
    for procedure_id in hidden_ids:
        if _purge_procedure(database, procedure_id, current):
            result["procedures_purged"] += 1
    for audit_id in audit_ids:
        if _purge_audit(database, audit_id):
            result["audits_purged"] += 1
    return result


def _recalculate_procedure(database: Database, procedure_id: str, now: datetime) -> bool:
    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT strength,importance_prior,reinforcement_count,last_reinforced_at,low_score_sweeps "
            "FROM procedures WHERE procedure_id=? AND status='active'", (procedure_id,),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        strength, importance, reinforcement_count, last_reinforced_at, low_sweeps = row
        last = _parse(last_reinforced_at)
        usage = 1.0 - math.exp(-int(reinforcement_count) / 3.0)
        recency = math.exp(-max(0.0, (now - last).total_seconds() / 86400.0) * math.log(2) / 70.0)
        score = 100.0 * (0.40 * float(importance) + 0.35 * usage + 0.25 * recency)
        next_strength = strength
        next_low = int(low_sweeps)
        if score >= 70.0 and int(reinforcement_count) >= 2:
            next_strength, next_low = "strong", 0
        elif strength == "strong" and score < 35.0:
            next_low = int(low_sweeps) + 1
            if next_low >= 2:
                next_strength, next_low = "weak", 0
        else:
            next_low = 0 if strength == "weak" else int(low_sweeps)
        connection.execute(
            "UPDATE procedures SET usage_score=?,recency_score=?,score=?,strength=?,low_score_sweeps=? WHERE procedure_id=?",
            (usage, recency, score, next_strength, next_low, procedure_id),
        )
        connection.commit()
    return True


def list_memory_items(
    database: Database, *, user_id: str, layer: str, workspace_id: str | None = None,
    include_hidden: bool = False,
) -> list[dict[str, object]]:
    """Return only the public, owner-scoped fields for one memory layer."""
    with closing(database._connect()) as connection:
        if layer == "L1":
            rows = connection.execute(
                "SELECT namespace,field_key,value,updated_at FROM profile_fields WHERE user_id=? ORDER BY namespace,field_key",
                (user_id,),
            ).fetchall()
            return [
                {"handle": f"{row[0]}.{row[1]}", "namespace": row[0], "key": row[1], "value": row[2], "updated_at": row[3]}
                for row in rows
            ]
        if workspace_id is None:
            return []
        status_clause = "status IN ('active','hidden')" if include_hidden else "status='active'"
        rows = connection.execute(
            "SELECT procedure_id,rule,applies_to,scenario_key,scenario_label,recommended_skill_names_json,"
            "ordering_constraints_json,avoid_when,strength,status,expires_at,hidden_at FROM procedures "
            f"WHERE user_id=? AND workspace_id=? AND {status_clause} ORDER BY procedure_id",
            (user_id, workspace_id),
        ).fetchall()
    return [
        {
            "handle": row[0], "rule": row[1], "applies_to": row[2], "scenario_key": row[3],
            "scenario_label": row[4], "recommended_skill_names": _json_list(row[5]),
            "ordering_constraints": _json_list(row[6]), "avoid_when": row[7],
            "strength": row[8], "status": row[9], "expires_at": row[10], "hidden_at": row[11],
        }
        for row in rows
    ]


def export_memory(
    database: Database, *, user_id: str, workspace_id: str,
) -> dict[str, object]:
    """Export user-visible memory, including pending candidates, without internals."""
    profiles = list_memory_items(database, user_id=user_id, layer="L1")
    procedures = list_memory_items(database, user_id=user_id, layer="L2", workspace_id=workspace_id)
    with closing(database._connect()) as connection:
        rows = connection.execute(
            "SELECT candidate_id,layer,kind,payload_json,created_at,expires_at FROM memory_candidates "
            "WHERE user_id=? AND workspace_id=? AND status='pending' ORDER BY created_at,candidate_id LIMIT 50",
            (user_id, workspace_id),
        ).fetchall()
    pending = []
    for row in rows:
        payload = json.loads(row[3])
        if isinstance(payload, dict):
            payload.pop("evidence_event_ids", None)
            payload.pop("source_run_id", None)
        pending.append({
            "candidate_id": row[0], "layer": row[1], "kind": row[2],
            "payload": payload, "created_at": row[4], "expires_at": row[5],
        })
    return {"profile_fields": profiles, "active_procedures": procedures, "pending_candidates": pending}


def delete_memory_item(
    database: Database, *, user_id: str, layer: str, handle: str, workspace_id: str | None = None,
) -> dict[str, object]:
    """Delete one exact Profile or Procedure handle and retain only audit metadata."""
    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if layer == "L1":
            parts = handle.split(".", 1)
            row = connection.execute(
                "SELECT profile_field_id FROM profile_fields WHERE user_id=? AND namespace=? AND field_key=?",
                (user_id, parts[0], parts[1]),
            ).fetchone() if len(parts) == 2 else None
            scope = "user_global"
        else:
            row = connection.execute(
                "SELECT procedure_id FROM procedures WHERE procedure_id=? AND user_id=? AND workspace_id=?",
                (handle, user_id, workspace_id),
            ).fetchone()
            scope = "workspace"
        if row is None:
            connection.rollback()
            raise ValueError("not_found")
        if layer == "L1":
            connection.execute("DELETE FROM profile_fields WHERE profile_field_id=?", (row[0],))
            reason = "profile_deleted_by_user"
        else:
            connection.execute("DELETE FROM procedures WHERE procedure_id=?", (row[0],))
            reason = "procedure_deleted_by_user"
        _audit(connection, scope, workspace_id, reason, handle, "user_requested", datetime.now(UTC))
        connection.commit()
    return {"deleted_handles": [f"{layer}:{handle}"], "audit_retained_count": 1, "completed_at": _format(datetime.now(UTC))}


def clear_profile(database: Database, *, user_id: str) -> dict[str, object]:
    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        profile_handles = [f"{row[0]}.{row[1]}" for row in connection.execute(
            "SELECT namespace,field_key FROM profile_fields WHERE user_id=?", (user_id,)
        ).fetchall()]
        candidates = [row[0] for row in connection.execute(
            "SELECT candidate_id FROM memory_candidates WHERE user_id=? AND layer='profile'", (user_id,)
        ).fetchall()]
        connection.execute("DELETE FROM profile_fields WHERE user_id=?", (user_id,))
        connection.execute("DELETE FROM memory_candidates WHERE user_id=? AND layer='profile'", (user_id,))
        now = datetime.now(UTC)
        for handle in profile_handles + [f"candidate:{item}" for item in candidates]:
            _audit(connection, "user_global", None, "profile_cleared", handle, "user_requested", now)
        connection.commit()
    return {"deleted_handles": profile_handles + [f"candidate:{item}" for item in candidates], "audit_retained_count": len(profile_handles) + len(candidates), "completed_at": _format(datetime.now(UTC))}


def clear_workspace_data(database: Database, *, user_id: str, workspace_id: str) -> dict[str, object]:
    """Immediately erase the complete user-owned workspace graph.

    The lifecycle sweeper only handles TTL transitions.  An explicit clear is
    stronger: it removes traces/run graph, learning weights, procedures and
    workspace candidates in one transaction.  Foreign-key cascades remove
    route decisions, bindings, episodes and outcomes attached to runs/traces.
    """
    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        run_ids = [row[0] for row in connection.execute(
            "SELECT run_id FROM run_contexts WHERE user_id=? AND workspace_id=?", (user_id, workspace_id)
        ).fetchall()]
        trace_ids = [row[0] for row in connection.execute(
            "SELECT turn_trace_id FROM turn_traces WHERE workspace_id=?", (workspace_id,)
        ).fetchall()]
        weight_names = [row[0] for row in connection.execute(
            "SELECT skill_name FROM skill_weights WHERE workspace_id=? ORDER BY skill_name", (workspace_id,)
        ).fetchall()]
        procedures = [row[0] for row in connection.execute(
            "SELECT procedure_id FROM procedures WHERE user_id=? AND workspace_id=?", (user_id, workspace_id)
        ).fetchall()]
        candidates = [row[0] for row in connection.execute(
            "SELECT candidate_id FROM memory_candidates WHERE user_id=? AND workspace_id=?", (user_id, workspace_id)
        ).fetchall()]
        # Delete explicit children first where no ON DELETE CASCADE exists.
        connection.execute("DELETE FROM route_offers WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM turn_traces WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM run_contexts WHERE user_id=? AND workspace_id=?", (user_id, workspace_id))
        connection.execute("DELETE FROM skill_weight_updates WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM skill_weights WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM memory_write_breakers WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM procedures WHERE user_id=? AND workspace_id=?", (user_id, workspace_id))
        connection.execute("DELETE FROM memory_candidates WHERE user_id=? AND workspace_id=?", (user_id, workspace_id))
        now = datetime.now(UTC)
        handles = (
            [f"run:{item}" for item in run_ids]
            + [f"trace:{item}" for item in trace_ids]
            + [f"weight:{item}" for item in weight_names]
            + procedures
            + [f"candidate:{item}" for item in candidates]
        )
        for handle in handles:
            _audit(connection, "workspace", workspace_id, "workspace_data_cleared", handle, "user_requested", now)
        connection.commit()
    return {"deleted_handles": handles, "audit_retained_count": len(handles), "completed_at": _format(datetime.now(UTC))}


def _expire_candidate(database: Database, candidate_id: str, now: datetime) -> bool:
    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT workspace_id FROM memory_candidates WHERE candidate_id=? AND status='pending' AND expires_at<=?",
            (candidate_id, _format(now)),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        connection.execute("DELETE FROM memory_candidates WHERE candidate_id=?", (candidate_id,))
        _audit(connection, "workspace", row[0], "candidate_expired", candidate_id, "ttl_expired", now)
        connection.commit()
    return True


def _hide_procedure(database: Database, procedure_id: str, now: datetime) -> bool:
    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT workspace_id FROM procedures WHERE procedure_id=? AND status='active' AND expires_at<=?",
            (procedure_id, _format(now)),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        hidden_at = _format(now)
        retention = _format(now + _HIDDEN_RETENTION)
        connection.execute(
            "UPDATE procedures SET status='hidden',hidden_at=?,retention_until=?,updated_at=? WHERE procedure_id=?",
            (hidden_at, retention, hidden_at, procedure_id),
        )
        _audit(connection, "workspace", row[0], "procedure_hidden", procedure_id, "ttl_expired", now)
        connection.commit()
    return True


def _purge_procedure(database: Database, procedure_id: str, now: datetime) -> bool:
    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT workspace_id FROM procedures WHERE procedure_id=? AND status='hidden' AND retention_until<=?",
            (procedure_id, _format(now)),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        connection.execute("DELETE FROM procedures WHERE procedure_id=?", (procedure_id,))
        _audit(connection, "workspace", row[0], "procedure_purged", procedure_id, "retention_expired", now)
        connection.commit()
    return True


def _purge_audit(database: Database, audit_id: str) -> bool:
    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        changed = connection.execute("DELETE FROM audit_events WHERE audit_id=? AND retention_until<=?", (audit_id, _format(datetime.now(UTC)))).rowcount
        connection.commit()
    return changed == 1


def _json_list(value: str) -> list[object]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _audit(
    connection: sqlite3.Connection, scope: str, workspace_id: str | None,
    event_type: str, handle: str, reason: str, now: datetime,
) -> None:
    connection.execute(
        "INSERT INTO audit_events(audit_id,scope,workspace_id,event_type,object_handle_hash,reason_code,policy_version,created_at,retention_until) VALUES (?,?,?,?,?,?,?,?,?)",
        (str(uuid4()), scope, workspace_id, event_type, _hash(handle), reason, _POLICY, _format(now), _format(now + _AUDIT_RETENTION)),
    )


def _hash(value: str) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _format(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
