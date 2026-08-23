"""Deterministic, scope-bound P5 procedure recall."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from contextlib import closing

from skilltree.core.storage import Database


_TOKEN = re.compile(r"[\w]+", re.UNICODE)
_SCAN_LIMIT = 50
_RESULT_LIMIT = 8
_SCENARIO_BONUS = 0.10


def recall_procedures(
    database: Database, *, query_summary: str, user_id: str, workspace_id: str,
    applies_to: str, scenario_key: str = "",
) -> list[dict[str, object]]:
    """Return only active, non-expired, text-relevant owner-scoped procedures."""
    query_tokens = _tokens(query_summary)
    if not query_tokens:
        return []
    try:
        with closing(database._connect()) as connection:
            enabled = connection.execute(
                "SELECT memory_read_enabled FROM runtime_config WHERE config_id=1"
            ).fetchone()
            if enabled is None or not bool(enabled[0]):
                return []
            rows = connection.execute(
                "SELECT rule,applies_to,scenario_key,scenario_label,recommended_skill_names_json,"
                "ordering_constraints_json,avoid_when,strength,score FROM procedures "
                "WHERE workspace_id=? AND user_id=? AND applies_to=? AND status='active' "
                "AND expires_at>? ORDER BY score DESC,updated_at DESC LIMIT ?",
                (workspace_id, user_id, applies_to, _utc_now(), _SCAN_LIMIT),
            ).fetchall()
    except sqlite3.Error:
        return []
    ranked: list[tuple[float, float, dict[str, object]]] = []
    for row in rows:
        rule, stored_applies_to, stored_scenario, label, skills, constraints, avoid, strength, score = row
        relevance = _relevance(query_tokens, _tokens(rule))
        if relevance <= 0:
            continue
        scenario_bonus = _SCENARIO_BONUS if scenario_key and scenario_key == stored_scenario else 0.0
        public = {
            "rule": rule,
            "applies_to": stored_applies_to,
            "scenario_key": stored_scenario,
            "scenario_label": label,
            "recommended_skill_names": _json_list(skills),
            "ordering_constraints": _json_list(constraints),
            "avoid_when": avoid,
            "strength": strength,
            "relevance_score": relevance + scenario_bonus,
        }
        ranked.append((relevance + scenario_bonus, float(score), public))
    ranked.sort(key=lambda item: (-item[0], -item[1], str(item[2]["rule"])))
    return [item[2] for item in ranked[:_RESULT_LIMIT]]


def _tokens(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return set(_TOKEN.findall(normalized))


def _relevance(query: set[str], rule: set[str]) -> float:
    if not rule:
        return 0.0
    return len(query & rule) / len(query)


def _json_list(value: str) -> list[object]:
    import json

    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
