from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from skilltree.core.doctor import diagnose
from skilltree.core.replay_extension import ReplayExtensionError, install_extension, replay_diagnose, uninstall_extension
from skilltree.core.extension_registry import ExtensionRegistryError, list_extensions, set_extension_state, remove_extension
from skilltree.core.replay_capsules import sweep_replay_capsules
from skilltree.core.replay_evaluation import ReplayEvaluationError, run_evolve_scan
from skilltree.core.episode import record_outcome_assessment
from skilltree.core.storage import Database, RegistryStorageError, StorageInitializationError
from skilltree.core.outbox import AtomicOutbox, WriterLease
from skilltree.core.trace_flush import flush_trace_events
from skilltree.core.learning import apply_explicit_feedback, apply_outcome_assessment, list_weights, rebuild_weights
from skilltree.core.memory_extractors import CandidateLLMError, OpenAICompatibleCandidateLLM
from skilltree.core.memory_import import MemoryImportError, import_markdown_candidates
from skilltree.core.memory_store import (
    MemoryStoreError,
    approve_memory_candidate,
    extract_and_store_memory_candidates,
    list_memory_candidates,
    reject_memory_candidate,
)
from skilltree.core.memory_lifecycle import (
    clear_profile,
    clear_workspace_data,
    delete_memory_item,
    export_memory,
    list_memory_items,
    sweep_memory_lifecycle,
)
from skilltree.core.memory_candidates import MemoryCandidateSchemaError
from skilltree.interfaces.consent_io import ConsentInputError, load_consent_request
from skilltree.interfaces.registry_io import RegistryInputError, load_registry_request
from skilltree.interfaces.routing_io import RouteInputError, load_route_candidates_stdin, load_route_request
from skilltree.interfaces.trace_io import TraceInputError, load_trace_outcome_request
from skilltree.interfaces.learning_io import LearningInputError, load_learning_request
from skilltree.interfaces.memory_io import MemoryRequestError, load_memory_request
from skilltree.interfaces.replay_io import ReplayInputError, load_replay_request
from skilltree.interfaces.evolve_io import EvolveInputError, load_evolve_request
from skilltree.registry_service.registry import RegistryError, discover_setup_candidates, scan_skill_root


def default_data_dir() -> Path:
    configured = os.environ.get("SKILLTREE_DATA_DIR")
    if configured:
        return Path(configured)
    plugin_data = os.environ.get("PLUGIN_DATA")
    if plugin_data:
        return Path(plugin_data)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "SkillTree"
    return Path.home() / ".skilltree"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skilltree")
    subparsers = parser.add_subparsers(dest="command", required=True)
    storage = subparsers.add_parser("storage")
    storage_subparsers = storage.add_subparsers(dest="storage_command", required=True)
    initialize = storage_subparsers.add_parser("initialize")
    initialize.add_argument("--data-dir", type=Path, required=True)
    initialize.add_argument("--plugin-root", type=Path, required=True)
    initialize.add_argument("--target-schema-version", type=int, required=True)
    initialize.add_argument("--json", action="store_true")
    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--data-dir", type=Path, default=default_data_dir())
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--replay-json", action="store_true")
    replay = subparsers.add_parser("replay")
    replay_subparsers = replay.add_subparsers(dest="replay_command", required=True)
    for command in ("install-extension", "uninstall-extension", "enable-extension", "disable-extension", "remove-extension"):
        child = replay_subparsers.add_parser(command)
        child.add_argument("--data-dir", type=Path, default=default_data_dir())
        child.add_argument("--input", type=Path, required=True)
    replay_subparsers.add_parser("list-extensions").add_argument("--data-dir", type=Path, default=default_data_dir())
    evolve = subparsers.add_parser("evolve")
    evolve_subparsers = evolve.add_subparsers(dest="evolve_command", required=True)
    evolve_scan = evolve_subparsers.add_parser("scan")
    evolve_scan.add_argument("--data-dir", type=Path, default=default_data_dir())
    evolve_scan.add_argument("--input", type=Path, required=True)
    config = subparsers.add_parser("config")
    config_subparsers = config.add_subparsers(dest="config_command", required=True)
    for command in ("status", "set-consent"):
        child = config_subparsers.add_parser(command)
        child.add_argument("--data-dir", type=Path, default=default_data_dir())
        child.add_argument("--input", type=Path, required=True)
    registry = subparsers.add_parser("registry")
    registry_subparsers = registry.add_subparsers(dest="registry_command", required=True)
    for command in ("setup", "scan", "trust", "block", "status"):
        child = registry_subparsers.add_parser(command)
        child.add_argument("--data-dir", type=Path, default=default_data_dir())
        child.add_argument("--input", type=Path, required=True)
    route = subparsers.add_parser("route")
    route_subparsers = route.add_subparsers(dest="route_command", required=True)
    candidates = route_subparsers.add_parser("candidates")
    candidates.add_argument("--stdin", action="store_true", required=True)
    candidates.add_argument("--data-dir", type=Path, default=default_data_dir())
    for command in ("prepare", "commit"):
        child = route_subparsers.add_parser(command)
        child.add_argument("--data-dir", type=Path, default=default_data_dir())
        child.add_argument("--input", type=Path, required=True)
    trace = subparsers.add_parser("trace")
    trace_subparsers = trace.add_subparsers(dest="trace_command", required=True)
    outcome = trace_subparsers.add_parser("outcome")
    outcome.add_argument("--data-dir", type=Path, default=default_data_dir())
    outcome.add_argument("--input", type=Path, required=True)
    learning = subparsers.add_parser("learning")
    learning_subparsers = learning.add_subparsers(dest="learning_command", required=True)
    for command in ("feedback", "outcome"):
        child = learning_subparsers.add_parser(command)
        child.add_argument("--data-dir", type=Path, default=default_data_dir())
        child.add_argument("--input", type=Path, required=True)
    weights = learning_subparsers.add_parser("weights")
    weights.add_argument("--data-dir", type=Path, default=default_data_dir())
    weights.add_argument("--workspace-id", required=True)
    rebuild = learning_subparsers.add_parser("rebuild")
    rebuild.add_argument("--data-dir", type=Path, default=default_data_dir())
    rebuild.add_argument("--workspace-id", required=True)
    rebuild.add_argument("--as-of")
    memory = subparsers.add_parser("memory")
    memory_subparsers = memory.add_subparsers(dest="memory_command", required=True)
    memory_import = memory_subparsers.add_parser("import")
    memory_import.add_argument("--data-dir", type=Path, default=default_data_dir())
    memory_import.add_argument("--input", type=Path, required=True)
    memory_import.add_argument("--user-id", required=True)
    memory_import.add_argument("--workspace-id", required=True)
    memory_extract = memory_subparsers.add_parser("extract")
    memory_extract.add_argument("--data-dir", type=Path, default=default_data_dir())
    memory_extract.add_argument("--run-id", required=True)
    memory_profile_extract = memory_subparsers.add_parser("profile-extract")
    memory_profile_extract.add_argument("--data-dir", type=Path, default=default_data_dir())
    memory_profile_extract.add_argument("--input", type=Path, required=True)
    for command in ("candidate-list", "list", "approve", "reject", "delete", "export"):
        child = memory_subparsers.add_parser(command)
        child.add_argument("--data-dir", type=Path, default=default_data_dir())
        child.add_argument("--input", type=Path, required=True)
    for command in ("clear-profile", "clear-workspace-data"):
        child = subparsers.add_parser(command)
        child.add_argument("--data-dir", type=Path, default=default_data_dir())
        child.add_argument("--input", type=Path, required=True)
    maintenance = subparsers.add_parser("maintenance")
    maintenance_subparsers = maintenance.add_subparsers(dest="maintenance_command", required=True)
    for command in ("sweep", "flush-trace"):
        child = maintenance_subparsers.add_parser(command)
        child.add_argument("--data-dir", type=Path, default=default_data_dir())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    database = Database(data_dir / "skilltree.sqlite3")

    if args.command == "storage":
        try:
            status = database.initialize(
                args.plugin_root.expanduser().resolve(), target_schema_version=args.target_schema_version
            )
        except StorageInitializationError as error:
            return _emit({"status": "failed", "error": "database_initialize_failed"}, args.json, 6)
        return _emit({"status": status, "schema_versions": database.applied_migrations()}, args.json)

    if args.command == "config":
        try:
            request = load_consent_request(args.input, args.config_command)
            if args.config_command == "status":
                payload = database.runtime_consent_status()
            else:
                payload = database.set_runtime_consent(
                    request["expected_config_version"], request["consents"]
                )
        except (ConsentInputError, RegistryStorageError) as error:
            return _emit_registry_error(error.code if hasattr(error, "code") else str(error))
        return _emit_registry_success(payload)

    if args.command == "registry":
        try:
            request = load_registry_request(args.input, args.registry_command)
            if args.registry_command == "setup":
                forbidden_roots = [data_dir, Path.cwd(), *_runtime_plugin_roots(data_dir)]
                candidates = discover_setup_candidates(
                    request.get("provided_root"), forbidden_roots=forbidden_roots,
                )
                payload = database.configure_skill_root(
                    Path(request["selected_root"]), candidates, forbidden_roots=forbidden_roots,
                )
            elif args.registry_command == "scan":
                payload = database.apply_scan(scan_skill_root(database.configured_skill_root()))
            elif args.registry_command in {"trust", "block"}:
                payload = database.set_trust_state(
                    request["name"], request["content_hash"],
                    "trusted" if args.registry_command == "trust" else "blocked",
                )
            else:
                payload = database.registry_status()
        except (RegistryInputError, RegistryError, RegistryStorageError) as error:
            return _emit_registry_error(error.code if hasattr(error, "code") else str(error))
        return _emit_registry_success(payload)

    if args.command == "route":
        if args.route_command == "candidates":
            try:
                request = load_route_candidates_stdin(sys.stdin.buffer.read())
                payload = database.list_route_candidates(request["prompt"])
            except (RouteInputError, RegistryStorageError) as error:
                return _emit_registry_error(error.code if hasattr(error, "code") else str(error))
            return _emit_registry_success(payload)
        try:
            request = load_route_request(args.input, args.route_command)
            if args.route_command == "prepare":
                payload = database.prepare_route(
                    request["workspace_id"], request["session_id_hash"], request["prompt"]
                )
            else:
                payload = database.commit_route(
                    request["route_token"], request["workspace_id"], request["session_id_hash"], request["decision"]
                )
        except (RouteInputError, RegistryStorageError) as error:
            return _emit_registry_error(error.code if hasattr(error, "code") else str(error))
        return _emit_registry_success(payload)

    if args.command == "trace":
        try:
            request = load_trace_outcome_request(args.input)
            assessment_id = record_outcome_assessment(
                database,
                run_id=request["run_id"],
                turn_trace_id=request["turn_trace_id"],
                event_id=request["event_id"],
                source=request["source"],
                verdict=request["verdict"],
                outcome_summary=request["outcome_summary"],
                evidence_ref=request["evidence_ref"] or None,
                observed_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
        except TraceInputError:
            return _emit_registry_error("invalid_schema")
        except ValueError as error:
            return _emit_registry_error("correlation_missing" if str(error) == "outcome_unattributed" else str(error))
        return _emit_registry_success({
            "assessment_id": assessment_id,
            "run_id": request["run_id"],
            "turn_trace_id": request["turn_trace_id"],
            "verdict": request["verdict"],
        })

    if args.command == "learning":
        try:
            if args.learning_command in {"feedback", "outcome"}:
                request = load_learning_request(args.input, args.learning_command)
                if args.learning_command == "feedback":
                    payload = apply_explicit_feedback(
                        database,
                        workspace_id=request["workspace_id"],
                        skill_names=request["skill_names"],
                        action=request["action"],
                        evidence_handle=request["evidence_handle"],
                        occurred_at=request.get("occurred_at"),
                    )
                else:
                    payload = apply_outcome_assessment(
                        database,
                        workspace_id=request["workspace_id"],
                        assessment_handle=request["assessment_handle"],
                        verdict=request["verdict"],
                        coverage_state=request["coverage_state"],
                        executed_skills=request.get("executed_skills", []),
                        failed_skills=request.get("failed_skills", []),
                        selected_skill=request.get("selected_skill"),
                        occurred_at=request.get("occurred_at"),
                    )
            elif args.learning_command == "weights":
                payload = {"weights": list_weights(database, workspace_id=args.workspace_id)}
            else:
                payload = rebuild_weights(database, workspace_id=args.workspace_id, as_of=args.as_of)
        except (LearningInputError, RegistryStorageError) as error:
            return _emit_registry_error(error.code if hasattr(error, "code") else str(error))
        return _emit_registry_success(payload)

    if args.command == "memory":
        try:
            if args.memory_command == "import":
                payload = import_markdown_candidates(
                    database,
                    source=args.input,
                    user_id=args.user_id,
                    workspace_id=args.workspace_id,
                )
            elif args.memory_command == "extract":
                llm = OpenAICompatibleCandidateLLM.from_environment()
                payload = extract_and_store_memory_candidates(
                    database, run_id=args.run_id, llm=llm,
                )
            elif args.memory_command == "profile-extract":
                request = load_memory_request(args.input, args.memory_command)
                llm = OpenAICompatibleCandidateLLM.from_environment()
                from skilltree.core.memory_store import extract_and_store_profile_candidates
                payload = extract_and_store_profile_candidates(
                    database,
                    user_id=request["user_id"],
                    workspace_id=request["workspace_id"],
                    durable_preference_statements=tuple(request["durable_preference_statements"]),
                    transient_user_instructions=tuple(request["transient_user_instructions"]),
                    response_feedback=request["response_feedback"],
                    llm=llm,
                )
            else:
                request = load_memory_request(args.input, args.memory_command)
                if args.memory_command == "candidate-list":
                    payload = {"candidates": list_memory_candidates(
                        database, user_id=request["user_id"], workspace_id=request["workspace_id"]
                    )}
                elif args.memory_command == "approve":
                    payload = approve_memory_candidate(
                        database, candidate_id=request["candidate_id"], user_id=request["user_id"],
                        workspace_id=request["workspace_id"],
                    )
                elif args.memory_command == "reject":
                    payload = reject_memory_candidate(
                        database, candidate_id=request["candidate_id"], user_id=request["user_id"],
                        workspace_id=request["workspace_id"],
                    )
                elif args.memory_command == "list":
                    payload = {"layer": request["layer"], "items": list_memory_items(
                        database,
                        user_id=request["user_id"],
                        layer=request["layer"],
                        workspace_id=request.get("workspace_id"),
                        include_hidden=request.get("include_hidden", False),
                    )}
                elif args.memory_command == "delete":
                    payload = delete_memory_item(
                        database,
                        user_id=request["user_id"],
                        layer=request["layer"],
                        handle=request["handle"],
                        workspace_id=request.get("workspace_id"),
                    )
                elif args.memory_command == "export":
                    payload = export_memory(
                        database, user_id=request["user_id"], workspace_id=request["workspace_id"]
                    )
                else:
                    payload = {"candidates": list_memory_candidates(
                        database, user_id=request["user_id"], workspace_id=request["workspace_id"]
                    )}
        except (MemoryImportError, MemoryRequestError, MemoryStoreError, MemoryCandidateSchemaError) as error:
            return _emit_registry_error(error.code if hasattr(error, "code") else "invalid_schema")
        except CandidateLLMError as error:
            return _emit_registry_error(str(error))
        return _emit_registry_success(payload)

    if args.command in {"clear-profile", "clear-workspace-data"}:
        try:
            request = load_memory_request(args.input, args.command)
            if args.command == "clear-profile":
                payload = clear_profile(database, user_id=request["user_id"])
            else:
                payload = clear_workspace_data(
                    database, user_id=request["user_id"], workspace_id=request["workspace_id"]
                )
        except (MemoryRequestError, MemoryStoreError, ValueError) as error:
            return _emit_registry_error(error.code if hasattr(error, "code") else str(error))
        return _emit_registry_success(payload)

    if args.command == "maintenance":
        if args.maintenance_command == "flush-trace":
            report = flush_trace_events(
                database,
                AtomicOutbox(data_dir / "outbox"),
                WriterLease(data_dir / "writer.lock", owner_id=f"cli:{os.getpid()}", ttl_seconds=30),
                now=datetime.now(UTC),
            )
            return _emit_registry_success({
                "inserted": report.inserted,
                "duplicates": report.duplicates,
                "quarantined": report.quarantined,
                "retained": report.retained,
            })
        try:
            legacy = database.maintenance_sweep()
            payload = {**legacy, **sweep_memory_lifecycle(database), **sweep_replay_capsules(database, data_dir=data_dir)}
        except RegistryStorageError as error:
            return _emit_registry_error(error.code)
        return _emit_registry_success(payload)

    if args.command == "replay":
        if args.replay_command == "list-extensions":
            try:
                payload = {"extensions": [record.__dict__ for record in list_extensions(Database(args.data_dir / "skilltree.sqlite3"))]}
            except ExtensionRegistryError as error:
                return _emit_registry_error(error.code)
            return _emit_registry_success(payload)
        try:
            request = load_replay_request(args.input, args.replay_command)
            if args.replay_command == "install-extension":
                plugin_roots = _runtime_plugin_roots(data_dir)
                payload = install_extension(
                    data_dir,
                    Path(request["extension_root"]),
                    plugin_root=plugin_roots[0] if plugin_roots else None,
                )
            elif args.replay_command == "uninstall-extension":
                payload = uninstall_extension(data_dir)
            else:
                database = Database(data_dir / "skilltree.sqlite3")
                if args.replay_command == "remove-extension":
                    remove_extension(database, request["extension_id"])
                    payload = {"extension_id": request["extension_id"], "state": "removed"}
                else:
                    state = "enable" if args.replay_command == "enable-extension" else "disable"
                    payload = set_extension_state(database, request["extension_id"], state).__dict__
        except (ReplayInputError, ReplayExtensionError) as error:
            return _emit_registry_error(error.code)
        return _emit_registry_success(payload)


    if args.command == "evolve":
        try:
            request = load_evolve_request(args.input)
            state_path = data_dir / "replay-runtime-state.json"
            if not state_path.is_file() or not os.environ.get("SKILLTREE_DOCKER_PATH"):
                return _emit_registry_error("replay_runtime_unavailable")
            runtime_state = json.loads(state_path.read_text(encoding="utf-8"))
            payload = run_evolve_scan(
                database,
                data_dir=data_dir,
                workspace_id=request["workspace_id"],
                candidate_id=request["candidate_id"],
                episode_ids=request["episode_ids"],
                runtime_state=runtime_state,
                docker_path=Path(os.environ["SKILLTREE_DOCKER_PATH"]),
            )
        except (EvolveInputError, ReplayEvaluationError, OSError, ValueError) as error:
            return _emit_registry_error(error.code if hasattr(error, "code") else "replay_runtime_unavailable")
        return _emit_registry_success(payload)

    payload, exit_code = diagnose(data_dir, include_replay=args.replay_json)
    return _emit(payload, args.json or args.replay_json, exit_code)


def _emit(payload: dict[str, object], as_json: bool, exit_code: int = 0) -> int:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    return exit_code


def _emit_registry_success(data: dict[str, object]) -> int:
    print(json.dumps({"schema_version": "skilltree/v1", "ok": True, "data": data, "error": None}, sort_keys=True))
    return 0


def _emit_registry_error(code: str) -> int:
    print(json.dumps({
        "schema_version": "skilltree/v1",
        "ok": False,
        "data": None,
        "error": {"code": code, "message": code, "retryable": code == "memory_write_degraded"},
    }, sort_keys=True))
    return 2


def _runtime_plugin_roots(data_dir: Path) -> list[Path]:
    """Read only installation metadata to exclude the immutable Plugin tree."""
    state_path = data_dir / "runtime-state.json"
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    plugin_root = raw.get("plugin_root") if isinstance(raw, dict) else None
    if not isinstance(plugin_root, str):
        return []
    path = Path(plugin_root)
    return [path] if path.is_absolute() else []
