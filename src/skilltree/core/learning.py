"""Workspace-scoped Skill learning weights and deterministic rebuilds."""

from __future__ import annotations

import re
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Iterable
from uuid import uuid4

from skilltree.core.storage import Database, RegistryStorageError


RULE_VERSION = "learning/v1"
DECAY_RULE_VERSION = "decay/v1"
MIN_WEIGHT = -10
MAX_WEIGHT = 10
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def apply_explicit_feedback(
    database: Database,
    *,
    workspace_id: str,
    skill_names: Iterable[str],
    action: str,
    evidence_handle: str,
    occurred_at: str | None = None,
) -> dict[str, object]:
    """Apply one explicit selection, rejection, or switch atomically."""
    _validate_workspace(workspace_id)
    names = _names(skill_names)
    if action not in {"select", "reject", "switch"} or not names:
        raise RegistryStorageError("invalid_schema")
    if action == "switch" and len(names) != 2:
        raise RegistryStorageError("invalid_schema")
    evidence = _bounded_text(evidence_handle, "evidence_handle")
    timestamp = _timestamp(occurred_at)
    if action == "select":
        changes = [(names[0], 2)]
    elif action == "reject":
        changes = [(name, -2) for name in names]
    else:
        changes = [(names[0], -2), (names[1], 2)]

    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            updates = []
            for name, delta in changes:
                update = _apply_delta(
                    connection,
                    workspace_id=workspace_id,
                    skill_name=name,
                    delta=delta,
                    source_type="explicit_switch" if action == "switch" else f"explicit_{action}",
                    evidence_handle=evidence,
                    evidence_quality="direct",
                    occurred_at=timestamp,
                    rule_version=RULE_VERSION,
                )
                if update is not None:
                    updates.append(update)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {"changed": bool(updates), "updates": updates, "weights": list_weights(database, workspace_id=workspace_id)}


def apply_outcome_assessment(
    database: Database,
    *,
    workspace_id: str,
    assessment_handle: str,
    verdict: str,
    coverage_state: str,
    executed_skills: Iterable[str] = (),
    failed_skills: Iterable[str] = (),
    selected_skill: str | None = None,
    occurred_at: str | None = None,
) -> dict[str, object]:
    """Learn from persisted outcome evidence without trusting model claims."""
    _validate_workspace(workspace_id)
    if verdict not in {"success", "failed"} or coverage_state not in {"observed", "partial", "unobserved", "unattributed", "claimed_outcome"}:
        raise RegistryStorageError("invalid_schema")
    evidence = _bounded_text(assessment_handle, "assessment_handle")
    timestamp = _timestamp(occurred_at)
    executed = _optional_names(executed_skills)
    failed = _optional_names(failed_skills)
    selected = _name_or_none(selected_skill)
    quality = "strict" if coverage_state == "observed" else "relaxed"
    if coverage_state in {"unattributed", "claimed_outcome"}:
        targets: list[tuple[str, int]] = []
    elif verdict == "success":
        targets = [(name, 1) for name in executed]
        if not targets and selected:
            targets = [(selected, 1)]
    else:
        targets = [(name, -1) for name in failed]
        if not targets and not executed and selected:
            targets = [(selected, -1)]

    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            updates = []
            for name, delta in targets:
                update = _apply_delta(
                    connection,
                    workspace_id=workspace_id,
                    skill_name=name,
                    delta=delta,
                    source_type="outcome",
                    evidence_handle=evidence,
                    evidence_quality=quality,
                    occurred_at=timestamp,
                    rule_version=RULE_VERSION,
                )
                if update is not None:
                    updates.append(update)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {"changed": bool(updates), "updates": updates, "weights": list_weights(database, workspace_id=workspace_id)}


def decay_weights(database: Database, *, workspace_id: str, as_of: str | None = None, _rebuild: bool = False) -> dict[str, object]:
    """Apply each newly completed 30-day decay period at most once."""
    _validate_workspace(workspace_id)
    timestamp = _timestamp(as_of)
    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = connection.execute(
                "SELECT skill_name, weight, last_signal_at, last_decay_at FROM skill_weights WHERE workspace_id=?",
                (workspace_id,),
            ).fetchall()
            updates = []
            for name, weight, signal_at, decay_at in rows:
                if not signal_at:
                    continue
                signal = _parse_timestamp(signal_at)
                if _parse_timestamp(timestamp) < signal:
                    continue
                previous_boundary = _parse_timestamp(decay_at) if decay_at else signal
                elapsed_steps = max(0, int((_parse_timestamp(timestamp) - signal).total_seconds() // (30 * 86400)))
                applied_steps = max(0, int((previous_boundary - signal).total_seconds() // (30 * 86400)))
                pending = elapsed_steps - applied_steps
                if pending <= 0:
                    continue
                current = int(weight)
                for step in range(pending):
                    delta = -1 if current > 0 else 1 if current < 0 else 0
                    if delta:
                        new_weight = _clamp(current + delta)
                        handle = f"decay:{workspace_id}:{name}:{applied_steps + step + 1}"
                        update = _apply_delta(
                            connection,
                            workspace_id=workspace_id,
                            skill_name=name,
                            delta=delta,
                            source_type="decay",
                            evidence_handle=handle,
                            evidence_quality="direct",
                            occurred_at=timestamp,
                            rule_version=DECAY_RULE_VERSION,
                            expected_weight=current,
                            signal_at=signal_at,
                            decay_at=_format_timestamp(signal + timedelta(days=30 * (applied_steps + step + 1))),
                            record=not _rebuild,
                        )
                        if update is not None:
                            updates.append(update)
                        current = new_weight
                    else:
                        current = 0
                boundary = signal + timedelta(days=30 * elapsed_steps)
                connection.execute(
                    "UPDATE skill_weights SET last_decay_at=?, last_updated_at=? WHERE workspace_id=? AND skill_name=?",
                    (_format_timestamp(boundary), timestamp, workspace_id, name),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {"changed": bool(updates), "updates": updates, "weights": list_weights(database, workspace_id=workspace_id)}


def rebuild_weights(database: Database, *, workspace_id: str, as_of: str | None = None) -> dict[str, object]:
    """Rebuild current weights from non-decay evidence and recompute decay."""
    _validate_workspace(workspace_id)
    timestamp = _timestamp(as_of)
    with closing(database._connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            evidence = connection.execute(
                "SELECT skill_name, source_type, delta, evidence_handle, evidence_quality, rule_version, created_at "
                "FROM skill_weight_updates WHERE workspace_id=? AND source_type <> 'decay' AND created_at <= ? "
                "ORDER BY created_at, rowid",
                (workspace_id, timestamp),
            ).fetchall()
            connection.execute("DELETE FROM skill_weights WHERE workspace_id=?", (workspace_id,))
            for name, source_type, delta, handle, quality, rule_version, created_at in evidence:
                _apply_delta(
                    connection,
                    workspace_id=workspace_id,
                    skill_name=name,
                    delta=int(delta),
                    source_type=source_type,
                    evidence_handle=handle,
                    evidence_quality=quality,
                    occurred_at=created_at,
                    rule_version=rule_version,
                    record=False,
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    decay = decay_weights(database, workspace_id=workspace_id, as_of=timestamp, _rebuild=True)
    return {"changed": True, "decay": decay["updates"], "weights": list_weights(database, workspace_id=workspace_id)}


def list_weights(database: Database, *, workspace_id: str) -> list[dict[str, object]]:
    _validate_workspace(workspace_id)
    with closing(database._connect()) as connection:
        rows = connection.execute(
            "SELECT workspace_id, skill_name, weight, last_signal_at, last_decay_at, last_updated_at, rule_version "
            "FROM skill_weights WHERE workspace_id=? ORDER BY weight DESC, skill_name ASC",
            (workspace_id,),
        ).fetchall()
    return [
        {
            "workspace_id": row[0], "skill_name": row[1], "weight": row[2],
            "last_signal_at": row[3], "last_decay_at": row[4],
            "last_updated_at": row[5], "rule_version": row[6],
        }
        for row in rows
    ]


def _apply_delta(
    connection,
    *,
    workspace_id: str,
    skill_name: str,
    delta: int,
    source_type: str,
    evidence_handle: str,
    evidence_quality: str,
    occurred_at: str,
    rule_version: str,
    expected_weight: int | None = None,
    signal_at: str | None = None,
    decay_at: str | None = None,
    record: bool = True,
) -> dict[str, object] | None:
    existing = connection.execute(
        "SELECT 1 FROM skill_weight_updates WHERE workspace_id=? AND skill_name=? AND evidence_handle=? AND rule_version=?",
        (workspace_id, skill_name, evidence_handle, rule_version),
    ).fetchone()
    if existing is not None and record:
        return None
    row = connection.execute(
        "SELECT weight, last_signal_at, last_decay_at, created_at FROM skill_weights WHERE workspace_id=? AND skill_name=?",
        (workspace_id, skill_name),
    ).fetchone()
    old_weight = int(row[0]) if row else 0
    if expected_weight is not None and old_weight != expected_weight:
        old_weight = expected_weight
    new_weight = _clamp(old_weight + int(delta))
    current_signal = row[1] if row else signal_at
    if source_type != "decay" and (current_signal is None or _parse_timestamp(occurred_at) >= _parse_timestamp(current_signal)):
        current_signal = occurred_at
    current_decay = decay_at if source_type == "decay" else (row[2] if row else None)
    created_at = row[3] if row else occurred_at
    connection.execute(
        "INSERT INTO skill_weights(workspace_id,skill_name,weight,last_signal_at,last_decay_at,last_updated_at,created_at,rule_version) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,skill_name) DO UPDATE SET weight=excluded.weight,last_signal_at=excluded.last_signal_at,last_decay_at=excluded.last_decay_at,last_updated_at=excluded.last_updated_at,rule_version=excluded.rule_version",
        (workspace_id, skill_name, new_weight, current_signal, current_decay, occurred_at, created_at, rule_version),
    )
    if record:
        connection.execute(
            "INSERT INTO skill_weight_updates(update_id,workspace_id,skill_name,source_type,delta,old_weight,new_weight,evidence_handle,evidence_quality,rule_version,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid4()), workspace_id, skill_name, source_type, int(delta), old_weight, new_weight, evidence_handle, evidence_quality, rule_version, occurred_at),
        )
    return {"skill_name": skill_name, "delta": int(delta), "old_weight": old_weight, "new_weight": new_weight, "evidence_handle": evidence_handle, "evidence_quality": evidence_quality, "rule_version": rule_version}


def _names(values: Iterable[str]) -> list[str]:
    try:
        result = list(dict.fromkeys(values))
    except TypeError as error:
        raise RegistryStorageError("invalid_schema") from error
    if not result or any(not isinstance(value, str) or not _NAME_RE.fullmatch(value) for value in result):
        raise RegistryStorageError("invalid_schema")
    return result


def _optional_names(values: Iterable[str]) -> list[str]:
    try:
        values_list = list(values)
    except TypeError as error:
        raise RegistryStorageError("invalid_schema") from error
    return _names(values_list) if values_list else []


def _name_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise RegistryStorageError("invalid_schema")
    return value


def _bounded_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 512:
        raise RegistryStorageError("invalid_schema")
    return value


def _validate_workspace(value: str) -> None:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 256:
        raise RegistryStorageError("invalid_schema")


def _timestamp(value: str | None) -> str:
    if value is None:
        return _format_timestamp(datetime.now(UTC))
    return _format_timestamp(_parse_timestamp(_bounded_text(value, "timestamp")))


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as error:
        raise RegistryStorageError("invalid_schema") from error


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clamp(value: int) -> int:
    return max(MIN_WEIGHT, min(MAX_WEIGHT, value))
