"""Deterministic read-only evidence projection for P5 memory extraction."""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import dataclass

from skilltree.core.sanitize import sanitize_description
from skilltree.core.storage import Database
from skilltree.core.trace_events import tool_calls_complete


@dataclass(frozen=True)
class EvidenceBundle:
    schema_version: str
    run_id: str
    workspace_id: str
    user_id: str
    task_type: str
    scenario_key: str
    scenario_label: str
    recommended_skills: tuple[str, ...]
    observed_tool_steps: tuple[str, ...]
    outcome: str
    outcome_evidence_kind: str
    durable_preference_statements: tuple[str, ...]
    transient_user_instructions: tuple[str, ...]
    response_feedback: str
    evidence_event_ids: tuple[str, ...]
    coverage_state: str
    route_degraded: bool = False
    observed_tool_chain: tuple[dict[str, object], ...] = ()


def build_evidence_bundle(database: Database, *, run_id: str) -> EvidenceBundle | None:
    """Project one eligible persisted Run without writing or inferring facts."""
    with closing(database._connect()) as connection:
        consent = connection.execute(
            "SELECT memory_write_enabled FROM runtime_config WHERE config_id=1"
        ).fetchone()
        if consent is None or not bool(consent[0]):
            return None
        run = connection.execute(
            "SELECT workspace_id,user_id,memory_write_enabled FROM run_contexts WHERE run_id=?", (run_id,)
        ).fetchone()
        if run is None or not bool(run[2]):
            return None
        decision_row = connection.execute(
            "SELECT decision_json FROM route_decisions WHERE run_id=?", (run_id,)
        ).fetchone()
        if decision_row is None:
            return None
        try:
            decision = json.loads(decision_row[0])
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(decision, dict):
            return None
        route_degraded = bool(decision.get("degraded", False))
        intent = decision.get("intent")
        task_type = intent.get("name", "") if isinstance(intent, dict) and isinstance(intent.get("name"), str) else ""
        ordered = decision.get("ordered_skill_names")
        recommended = tuple(item for item in ordered if isinstance(item, str)) if isinstance(ordered, list) else ()
        events = connection.execute(
            "SELECT event_id,event_type,source,coverage_state,payload_summary,tool_use_id,tool_name "
            "FROM trace_events WHERE run_id=? ORDER BY ingest_sequence", (run_id,)
        ).fetchall()
        event_ids = tuple(row[0] for row in events)
        tool_phases = ((row[1], row[5]) for row in events)
        coverage = (
            "observed"
            if events and all(row[3] == "observed" for row in events) and tool_calls_complete(tool_phases)
            else "partial"
        )
        tool_chain = _build_tool_chain(events)
        steps = tuple(
            step
            for chain in tool_chain
            if (step := _tool_step_summary(chain))
        )
        semantic_steps = tuple(
            summary
            for chain in tool_chain
            for summary in chain.get("summaries", ())
            if isinstance(summary, str)
        )
        task_type, scenario_key, scenario_label = _derive_semantic_context(task_type, semantic_steps)
        feedback_rows = [row for row in events if row[1] == "user_feedback" and row[2] == "user"]
        feedback = "accepted" if feedback_rows else "none"
        outcome_row = connection.execute(
            "SELECT source,verdict FROM outcome_assessments WHERE run_id=? ORDER BY observed_at DESC,rowid DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if outcome_row is None:
            outcome, evidence_kind = "unknown", "none"
        else:
            source, outcome = outcome_row
            if outcome == "success" and source == "user":
                evidence_kind = "successful_delivery"
            elif outcome == "success" and coverage == "observed":
                evidence_kind = "successful_execution"
            else:
                evidence_kind = "none"
        return EvidenceBundle(
            schema_version="skilltree-evidence-bundle/v1",
            run_id=run_id,
            workspace_id=run[0],
            user_id=run[1],
            task_type=task_type,
            scenario_key=scenario_key,
            scenario_label=scenario_label,
            recommended_skills=recommended,
            observed_tool_steps=steps,
            outcome=outcome,
            outcome_evidence_kind=evidence_kind,
            durable_preference_statements=(),
            transient_user_instructions=(),
            response_feedback=feedback,
            evidence_event_ids=event_ids,
            coverage_state=coverage,
            route_degraded=route_degraded,
            observed_tool_chain=tool_chain,
        )


def _build_tool_chain(events: list[tuple[object, ...]]) -> tuple[dict[str, object], ...]:
    """Pair observed Tool phases without retaining input/output contents."""
    chains: dict[str, dict[str, object]] = {}
    order: list[str] = []
    for event_id, event_type, _source, _coverage, summary, tool_use_id, tool_name in events:
        if event_type not in {"tool_started", "tool_finished", "tool_failed"}:
            continue
        if not isinstance(tool_use_id, str) or not tool_use_id:
            continue
        chain = chains.get(tool_use_id)
        if chain is None:
            chain = {
                "tool_use_id": tool_use_id,
                "tool_name": tool_name if isinstance(tool_name, str) else "",
                "started_event_id": None,
                "finished_event_id": None,
                "failed_event_id": None,
                "status": "started",
                "summaries": [],
            }
            chains[tool_use_id] = chain
            order.append(tool_use_id)
        sanitized = sanitize_description(summary)
        if sanitized.state != "rejected":
            summaries = chain["summaries"]
            assert isinstance(summaries, list)
            summaries.append(sanitized.value)
        if event_type == "tool_started":
            ids = chain.setdefault("started_event_ids", [])
            assert isinstance(ids, list)
            ids.append(event_id)
            if chain["started_event_id"] is None:
                chain["started_event_id"] = event_id
            if len(ids) > 1:
                chain["status"] = "invalid_duplicate"
            elif chain["status"] not in {"finished", "failed"}:
                chain["status"] = "started"
        elif event_type == "tool_finished":
            ids = chain.setdefault("finished_event_ids", [])
            assert isinstance(ids, list)
            ids.append(event_id)
            if chain["finished_event_id"] is None:
                chain["finished_event_id"] = event_id
            if len(ids) > 1:
                chain["status"] = "invalid_duplicate"
            elif chain["status"] != "invalid_duplicate":
                chain["status"] = "finished"
        else:
            ids = chain.setdefault("failed_event_ids", [])
            assert isinstance(ids, list)
            ids.append(event_id)
            if chain["failed_event_id"] is None:
                chain["failed_event_id"] = event_id
            if len(ids) > 1:
                chain["status"] = "invalid_duplicate"
            elif chain["status"] != "invalid_duplicate":
                chain["status"] = "failed"
    result: list[dict[str, object]] = []
    for tool_use_id in order:
        chain = dict(chains[tool_use_id])
        chain["summaries"] = tuple(chain["summaries"])
        for key in ("started_event_ids", "finished_event_ids", "failed_event_ids"):
            ids = chain.get(key)
            if isinstance(ids, list) and len(ids) > 1:
                chain[key] = tuple(ids)
            else:
                chain.pop(key, None)
        result.append(chain)
    return tuple(result)


def _tool_step_summary(chain: dict[str, object]) -> str:
    """Return one bounded semantic step for one tool invocation."""
    summaries = chain.get("summaries")
    if not isinstance(summaries, (tuple, list)):
        return ""
    unique: list[str] = []
    for summary in summaries:
        if isinstance(summary, str) and summary and summary not in unique:
            unique.append(summary)
    return unique[0] if unique else ""


def _derive_semantic_context(task_type: str, steps: tuple[str, ...]) -> tuple[str, str, str]:
    """Derive a coarse, privacy-safe context from operation categories only."""
    categories = " ".join(steps).casefold()
    has_source_read = "read_source" in categories
    has_execution = any(item in categories for item in ("run_python", "run_tests"))
    if task_type in {"", "skill_routing"} and has_source_read and has_execution:
        return "repository_verification", "read_and_execute_verification", "读取源码并执行验证"
    return task_type, "", ""
