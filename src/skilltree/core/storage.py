from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from skilltree.core.bundle import BundleValidationError, validate_bundle
from skilltree.core.routing_catalog import (
    ROUTE_TOP_K,
    build_metadata_catalog,
    lexical_top_k,
)
from skilltree.registry_service.registry import REGISTRY_CAPACITY, RegistryError, ScannedSkill, validate_skill_root


_CONSENT_KEYS = (
    "trace_capture_enabled",
    "memory_read_enabled",
    "memory_write_enabled",
    "replay_capture_enabled",
)

class StorageInitializationError(RuntimeError):
    """Raised when a database cannot be safely initialized from a Bundle."""


class RegistryStorageError(RuntimeError):
    """Raised for a registry operation with a stable public error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self, plugin_root: Path, *, target_schema_version: int) -> str:
        """Apply the verified Manifest migration prefix in one SQLite transaction."""
        try:
            manifest = validate_bundle(plugin_root)
        except BundleValidationError as error:
            raise StorageInitializationError("bundle validation failed") from error
        if not isinstance(target_schema_version, int) or not 1 <= target_schema_version <= manifest["schema"]["migration_version"]:
            raise StorageInitializationError("target schema version is outside the bundle prefix")

        migrations = manifest["migrations"]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    applied = self._applied_hashes(connection)
                    manifest_hashes = {migration["version"]: migration["sha256"] for migration in migrations}
                    expected_prefix = set(range(1, len(applied) + 1))
                    if set(applied) != expected_prefix:
                        raise StorageInitializationError("migration history is not a bundle prefix")
                    if any(version > target_schema_version for version in applied):
                        raise StorageInitializationError("database schema version is higher than bundle")
                    for migration in migrations:
                        version = migration["version"]
                        if version > target_schema_version:
                            break
                        expected_hash = migration["sha256"]
                        recorded_hash = applied.get(version)
                        if recorded_hash is not None:
                            if recorded_hash != expected_hash:
                                raise StorageInitializationError("migration hash mismatch")
                            continue
                        sql = (plugin_root / migration["path"]).read_text(encoding="utf-8")
                        if "sha256:" + hashlib.sha256(sql.encode("utf-8")).hexdigest() != expected_hash:
                            raise StorageInitializationError("migration file hash mismatch")
                        _execute_statements(connection, sql)
                        connection.execute(
                            "INSERT INTO schema_migrations(version, applied_at, content_hash) VALUES (?, ?, ?)",
                            (version, _utc_now(), expected_hash),
                        )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except StorageInitializationError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise StorageInitializationError("database initialization failed") from error
        return "initialized"

    def applied_migrations(self) -> list[int]:
        if not self.path.is_file():
            return []
        with closing(self._connect()) as connection:
            return [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]

    def read_runtime_settings(self) -> dict[str, bool]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT trace_capture_enabled, memory_read_enabled, memory_write_enabled, replay_capture_enabled "
                "FROM runtime_config WHERE config_id = 1"
            ).fetchone()
        if row is None:
            return {}
        return dict(zip(("trace_capture_enabled", "memory_read_enabled", "memory_write_enabled", "replay_capture_enabled"), map(bool, row), strict=True))

    def runtime_consent_status(self) -> dict[str, object]:
        """Return the four user-controlled RuntimeConfig consent values."""
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT config_version, skill_root_hash, trace_capture_enabled, memory_read_enabled, "
                    "memory_write_enabled, replay_capture_enabled, updated_at "
                    "FROM runtime_config WHERE config_id = 1"
                ).fetchone()
        except sqlite3.Error as error:
            raise RegistryStorageError("internal_error") from error
        if row is None:
            raise RegistryStorageError("internal_error")
        return {
            "config_version": row[0],
            "skill_root_hash": row[1],
            "consents": dict(zip(_CONSENT_KEYS, map(bool, row[2:6]), strict=True)),
            "updated_at": row[6],
        }

    def set_runtime_consent(self, expected_config_version: int, consents: dict[str, object]) -> dict[str, object]:
        """Atomically replace all RuntimeConfig consents after explicit CLI validation."""
        if (
            not isinstance(expected_config_version, int)
            or isinstance(expected_config_version, bool)
            or expected_config_version < 1
            or set(consents) != set(_CONSENT_KEYS)
            or not all(isinstance(value, bool) for value in consents.values())
        ):
            raise RegistryStorageError("invalid_schema")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        "SELECT config_version, skill_root_hash, trace_capture_enabled, memory_read_enabled, "
                        "memory_write_enabled, replay_capture_enabled FROM runtime_config WHERE config_id = 1"
                    ).fetchone()
                    if row is None:
                        raise RegistryStorageError("internal_error")
                    if row[0] != expected_config_version:
                        raise RegistryStorageError("conflict")
                    current = dict(zip(_CONSENT_KEYS, map(bool, row[2:6]), strict=True))
                    changed_keys = [key for key in _CONSENT_KEYS if current[key] != consents[key]]
                    completed_at = _utc_now()
                    version = row[0]
                    if changed_keys:
                        version += 1
                        connection.execute(
                            "UPDATE runtime_config SET trace_capture_enabled = ?, memory_read_enabled = ?, "
                            "memory_write_enabled = ?, replay_capture_enabled = ?, config_version = ?, updated_at = ? "
                            "WHERE config_id = 1",
                            (*[int(consents[key]) for key in _CONSENT_KEYS], version, completed_at),
                        )
                        for key in changed_keys:
                            _write_audit(
                                connection,
                                "runtime_consent_changed",
                                _hash_text(f"runtime_config/{key}"),
                                "enabled" if consents[key] else "disabled",
                                "runtime-consent/v1",
                            )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except RegistryStorageError:
            raise
        except sqlite3.Error as error:
            raise RegistryStorageError("internal_error") from error
        return {
            "config_version": version,
            "consents": {key: bool(consents[key]) for key in _CONSENT_KEYS},
            "changed_keys": changed_keys,
            "completed_at": completed_at,
        }

    def configured_skill_root(self) -> Path:
        """Return the currently configured root, without discovering alternatives."""
        try:
            with closing(self._connect()) as connection:
                root = _configured_root(connection)
        except sqlite3.Error as error:
            raise RegistryStorageError("internal_error") from error
        if root is None:
            raise RegistryStorageError("authorization_required")
        try:
            return validate_skill_root(root)
        except ValueError:
            raise RegistryStorageError("authorization_required") from None

    def configure_skill_root(
        self,
        selected_root: Path,
        candidate_roots: list[Path],
        *,
        forbidden_roots: list[Path] | tuple[Path, ...] = (),
    ) -> dict[str, object]:
        """Persist an explicitly selected candidate root and retire old entries."""
        try:
            selected = validate_skill_root(selected_root, forbidden_roots=forbidden_roots)
            candidates = [validate_skill_root(candidate, forbidden_roots=forbidden_roots) for candidate in candidate_roots]
        except RegistryError as error:
            raise RegistryStorageError(error.code) from error
        if selected not in candidates:
            raise RegistryStorageError("out_of_scope")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        "SELECT config_version, skill_root, skill_root_hash FROM runtime_config WHERE config_id = 1"
                    ).fetchone()
                    if row is None:
                        raise RegistryStorageError("internal_error")
                    config_version, old_root, old_hash = row
                    root_hash = _hash_text(str(selected))
                    if old_root != str(selected):
                        connection.execute(
                            "UPDATE skills SET state = 'out_of_scope', updated_at = ? WHERE state != 'out_of_scope'",
                            (_utc_now(),),
                        )
                        config_version += 1
                        connection.execute(
                            "UPDATE runtime_config SET skill_root = ?, skill_root_hash = ?, config_version = ?, updated_at = ? "
                            "WHERE config_id = 1",
                            (str(selected), root_hash, config_version, _utc_now()),
                        )
                        _write_audit(connection, "skill_root_configured", root_hash, "configured", "registry/v1")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except RegistryStorageError:
            raise
        except sqlite3.Error as error:
            raise RegistryStorageError("internal_error") from error
        return {
            "candidate_root_hashes": [_hash_text(str(candidate)) for candidate in candidates],
            "skill_root_hash": root_hash if old_root != str(selected) else old_hash,
            "config_version": config_version,
            "completed_at": _utc_now(),
        }

    def apply_scan(self, records: list[ScannedSkill]) -> dict[str, object]:
        """Atomically apply a complete, capacity-bounded scan result."""
        if len(records) > REGISTRY_CAPACITY:
            raise RegistryStorageError("registry_capacity_exceeded")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    configured_root = _configured_root(connection)
                    if configured_root is None:
                        raise RegistryStorageError("authorization_required")
                    try:
                        root = validate_skill_root(configured_root)
                    except ValueError:
                        raise RegistryStorageError("authorization_required") from None
                    current_paths: set[str] = set()
                    for record in records:
                        path = record.path.resolve(strict=True)
                        if not _is_within_root(path, root):
                            raise RegistryStorageError("out_of_scope")
                        current_paths.add(str(path))
                        existing = connection.execute(
                            "SELECT path, content_hash, state FROM skills WHERE name = ?", (record.name,)
                        ).fetchone()
                        if existing is None:
                            legacy = connection.execute(
                                "SELECT name FROM skills WHERE path = ?", (str(path),)
                            ).fetchone()
                            if legacy is not None and legacy[0] != record.name:
                                # A previous bundle may have derived invalid names from paths.
                                # Remove only that same-path legacy row before applying the new
                                # content-hash-derived identity.
                                connection.execute("DELETE FROM skills WHERE name = ?", (legacy[0],))
                        # Every current file under the explicitly configured user root is
                        # trusted by the user's opt-in policy. The original file is never
                        # rewritten; only the registry projection is normalized.
                        state = "trusted"
                        connection.execute(
                            "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(name) DO UPDATE SET description = excluded.description, path = excluded.path, "
                            "content_hash = excluded.content_hash, state = excluded.state, diagnostic = excluded.diagnostic, "
                            "updated_at = excluded.updated_at",
                            (
                                record.name,
                                record.description,
                                str(path),
                                record.content_hash,
                                state,
                                record.diagnostic,
                                _utc_now(),
                            ),
                        )
                    for name, path, description in connection.execute(
                        "SELECT name, path, description FROM skills WHERE state != 'out_of_scope'"
                    ):
                        if _stored_path_is_within_root(path, root) and path not in current_paths:
                            connection.execute(
                                "UPDATE skills SET state = 'out_of_scope', description = ?, updated_at = ? WHERE name = ?",
                                (description or "Unvalidated Skill metadata", _utc_now(), name),
                            )
                    _write_audit(
                        connection,
                        "registry_scan_applied",
                        _hash_text(str(root)),
                        "scanned",
                        "registry/v1",
                    )
                    counts = _registry_counts(connection)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except RegistryStorageError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise RegistryStorageError("internal_error") from error
        return {"scanned_count": len(records), **counts, "completed_at": _utc_now()}

    def set_trust_state(self, name: str, content_hash: str, state: str) -> dict[str, str]:
        """Move one exact pending entry to trusted or blocked."""
        if state not in {"trusted", "blocked"}:
            raise RegistryStorageError("invalid_schema")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        "SELECT content_hash, state FROM skills WHERE name = ?", (name,)
                    ).fetchone()
                    if row is None:
                        raise RegistryStorageError("not_found")
                    if row[1] in {"invalid", "out_of_scope"}:
                        raise RegistryStorageError("out_of_scope")
                    if row[0] != content_hash or row[1] != "pending":
                        raise RegistryStorageError("conflict")
                    connection.execute(
                        "UPDATE skills SET state = ?, updated_at = ? WHERE name = ?",
                        (state, _utc_now(), name),
                    )
                    _write_audit(
                        connection,
                        "skill_trust_state_changed",
                        _hash_text(f"{name}/{content_hash}"),
                        state,
                        "registry/v1",
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except RegistryStorageError:
            raise
        except sqlite3.Error as error:
            raise RegistryStorageError("internal_error") from error
        return {"name": name, "content_hash": content_hash, "state": state, "completed_at": _utc_now()}

    def registry_status(self) -> dict[str, object]:
        """Return status-safe registry data without exposing filesystem paths."""
        try:
            with closing(self._connect()) as connection:
                config = connection.execute(
                    "SELECT config_version, skill_root_hash FROM runtime_config WHERE config_id = 1"
                ).fetchone()
                if config is None:
                    raise RegistryStorageError("internal_error")
                skills = [
                    {
                        "name": name,
                        "description": description,
                        "content_hash": content_hash,
                        "state": state,
                        "diagnostic_code": diagnostic,
                        "updated_at": updated_at,
                    }
                    for name, description, content_hash, state, diagnostic, updated_at in connection.execute(
                        "SELECT name, description, content_hash, state, diagnostic, updated_at "
                        "FROM skills ORDER BY name ASC LIMIT ?",
                        (REGISTRY_CAPACITY,),
                    )
                ]
        except RegistryStorageError:
            raise
        except sqlite3.Error as error:
            raise RegistryStorageError("internal_error") from error
        return {"skill_root_hash": config[1], "config_version": config[0], "skills": skills, "count": len(skills)}

    def prepare_route(self, workspace_id: str, session_id_hash: str, prompt: str) -> dict[str, object]:
        """Create a five-minute RouteOffer from trusted skills without storing prompt text."""
        if not _is_sha256_value(workspace_id) or not _is_sha256_value(session_id_hash):
            raise RegistryStorageError("invalid_schema")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    trusted = [
                        {"name": name, "description": description, "content_hash": content_hash, "path": path}
                        for name, description, content_hash, path in connection.execute(
                            "SELECT name, description, content_hash, path FROM skills WHERE state IN ('trusted', 'pending', 'invalid') ORDER BY name ASC"
                        )
                    ]
                    candidates, degraded = _route_candidates(trusted, prompt)
                    if not candidates:
                        raise RegistryStorageError("not_found")
                    route_token = secrets.token_urlsafe(32)
                    token_hash = _hash_text(route_token)
                    candidate_json = _canonical_json(candidates)
                    candidate_hash = _hash_text(candidate_json)
                    now = datetime.now(UTC)
                    prepared_at = _format_utc(now)
                    expires_at = _format_utc(now + timedelta(minutes=5))
                    trusted_snapshot = _canonical_json(trusted)
                    connection.execute(
                        "INSERT INTO route_offers(route_token_hash, workspace_id, session_id_hash, provisional_run_id, "
                        "trusted_snapshot_json, candidate_json, candidate_snapshot_hash, prepared_at, expires_at, retention_until) "
                        "VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                        (token_hash, workspace_id, session_id_hash, trusted_snapshot, candidate_json, candidate_hash,
                         prepared_at, expires_at, expires_at),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except RegistryStorageError:
            raise
        except sqlite3.Error as error:
            raise RegistryStorageError("internal_error") from error
        return {
            "schema_version": "skilltree-route-envelope/v1",
            "route_token": route_token,
            "expires_at": expires_at,
            "candidate_snapshot_hash": candidate_hash,
            "candidates": candidates,
            "degraded": degraded,
        }

    def list_route_candidates(self, prompt: str) -> dict[str, object]:
        """Return trusted route candidates without creating an offer or token."""
        if not isinstance(prompt, str) or not prompt or len(prompt.encode("utf-8")) > 16 * 1024:
            raise RegistryStorageError("invalid_schema")
        try:
            with closing(self._connect()) as connection:
                settings = connection.execute(
                    "SELECT trace_capture_enabled FROM runtime_config WHERE config_id = 1"
                ).fetchone()
                if settings is None:
                    raise RegistryStorageError("internal_error")
                if not bool(settings[0]):
                    raise RegistryStorageError("authorization_required")
                trusted = [
                    {"name": name, "description": description, "content_hash": content_hash, "path": path}
                    for name, description, content_hash, path in connection.execute(
                        "SELECT name, description, content_hash, path FROM skills WHERE state IN ('trusted', 'pending', 'invalid') ORDER BY name ASC"
                    )
                ]
        except RegistryStorageError:
            raise
        except sqlite3.Error as error:
            raise RegistryStorageError("internal_error") from error
        candidates, _ = _route_candidates(trusted, prompt)
        if not candidates:
            raise RegistryStorageError("not_found")
        return {
            "schema_version": "skilltree-route-candidates/v1",
            "candidates": candidates,
            "degraded": True,
        }

    def commit_route(
        self,
        route_token: str,
        workspace_id: str,
        session_id_hash: str,
        decision: dict[str, object],
    ) -> dict[str, object]:
        """Atomically consume one RouteOffer and persist its validated decision."""
        if not isinstance(route_token, str) or not _is_sha256_value(workspace_id) or not _is_sha256_value(session_id_hash):
            raise RegistryStorageError("invalid_schema")
        token_hash = _hash_text(route_token)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    offer = connection.execute(
                        "SELECT workspace_id, session_id_hash, provisional_run_id, trusted_snapshot_json, candidate_json, "
                        "candidate_snapshot_hash, expires_at FROM route_offers WHERE route_token_hash = ?",
                        (token_hash,),
                    ).fetchone()
                    if offer is None:
                        raise RegistryStorageError("conflict")
                    (
                        offer_workspace_id,
                        offer_session_id_hash,
                        provisional_run_id,
                        trusted_snapshot_json,
                        candidate_json,
                        candidate_snapshot_hash,
                        expires_at,
                    ) = offer
                    if offer_workspace_id != workspace_id or offer_session_id_hash != session_id_hash:
                        raise RegistryStorageError("conflict")
                    if _parse_utc(expires_at) <= datetime.now(UTC):
                        raise RegistryStorageError("route_token_invalid")
                    if _hash_text(candidate_json) != candidate_snapshot_hash:
                        raise RegistryStorageError("conflict")
                    try:
                        candidates = json.loads(candidate_json)
                    except json.JSONDecodeError as error:
                        raise RegistryStorageError("conflict") from error
                    decision = _normalize_route_decision(decision, candidates)
                    _validate_route_decision(decision, candidates)
                    committed_at = _utc_now()
                    retention_until = _format_utc(datetime.now(UTC) + timedelta(days=90))
                    run_id = provisional_run_id
                    if run_id is None:
                        settings = connection.execute(
                            "SELECT trace_capture_enabled, memory_read_enabled, memory_write_enabled, replay_capture_enabled "
                            "FROM runtime_config WHERE config_id = 1"
                        ).fetchone()
                        if settings is None:
                            raise RegistryStorageError("internal_error")
                        run_id = str(uuid4())
                        connection.execute(
                            "INSERT INTO run_contexts(run_id, workspace_id, user_id, snapshot_json, trace_capture_enabled, "
                            "memory_read_enabled, memory_write_enabled, replay_capture_enabled, created_at, retention_until) "
                            "VALUES (?, ?, 'local', ?, ?, ?, ?, ?, ?, ?)",
                            (run_id, workspace_id, trusted_snapshot_json, *settings, committed_at, retention_until),
                        )
                    else:
                        existing_run = connection.execute(
                            "SELECT 1 FROM run_contexts WHERE run_id = ? AND workspace_id = ?",
                            (run_id, workspace_id),
                        ).fetchone()
                        if existing_run is None:
                            raise RegistryStorageError("conflict")
                    decision_json = _canonical_json(decision)
                    connection.execute(
                        "INSERT INTO route_decisions(run_id, route_token_hash, candidate_snapshot_hash, decision_json, committed_at, "
                        "retention_until) VALUES (?, ?, ?, ?, ?, ?)",
                        (run_id, token_hash, candidate_snapshot_hash, decision_json, committed_at, retention_until),
                    )
                    connection.execute("DELETE FROM route_offers WHERE route_token_hash = ?", (token_hash,))
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except RegistryStorageError:
            raise
        except sqlite3.Error as error:
            raise RegistryStorageError("internal_error") from error
        return {
            "schema_version": "skilltree/v1",
            "run_id": run_id,
            "selected_skill_name": decision["selected_skill_name"],
            "committed_at": committed_at,
        }

    def commit_current_route(
        self,
        workspace_id: str,
        session_id: str,
        turn_id: str | None,
        decision: dict[str, object] | None,
    ) -> dict[str, object]:
        """Commit a compact fallback decision to exactly one current-turn offer."""
        if (
            not _is_sha256_value(workspace_id)
            or not isinstance(session_id, str)
            or not session_id
            or (turn_id is not None and (not isinstance(turn_id, str) or not turn_id))
        ):
            raise RegistryStorageError("invalid_schema")
        now = datetime.now(UTC)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    rows = connection.execute(
                        "SELECT o.route_token_hash, o.provisional_run_id, o.trusted_snapshot_json, "
                        "o.candidate_json, o.candidate_snapshot_hash, o.expires_at "
                        "FROM route_offers AS o "
                        "JOIN run_turn_bindings AS b ON b.run_id = o.provisional_run_id "
                        "JOIN turn_traces AS t ON t.turn_trace_id = b.turn_trace_id "
                        "WHERE o.workspace_id = ? AND t.workspace_id = ? AND t.session_id = ? "
                        + ("AND t.turn_id = ? " if turn_id is not None else "")
                        + "AND o.expires_at > ?",
                        ((workspace_id, workspace_id, session_id, turn_id, _format_utc(now))
                         if turn_id is not None
                         else (workspace_id, workspace_id, session_id, _format_utc(now))),
                    ).fetchall()
                    if len(rows) != 1:
                        raise RegistryStorageError("correlation_missing")
                    token_hash, provisional_run_id, trusted_snapshot_json, candidate_json, candidate_snapshot_hash, expires_at = rows[0]
                    if _parse_utc(expires_at) <= now:
                        raise RegistryStorageError("route_token_invalid")
                    if _hash_text(candidate_json) != candidate_snapshot_hash:
                        raise RegistryStorageError("conflict")
                    try:
                        candidates = json.loads(candidate_json)
                    except json.JSONDecodeError as error:
                        raise RegistryStorageError("conflict") from error
                    if decision is None:
                        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
                            raise RegistryStorageError("correlation_missing")
                        selected_name = candidates[0].get("name")
                        if not isinstance(selected_name, str) or not selected_name:
                            raise RegistryStorageError("conflict")
                        decision = {
                            "selected_skill_name": selected_name,
                            "ordered_skill_names": [selected_name],
                        }
                    decision = _normalize_route_decision(decision, candidates)
                    _validate_route_decision(decision, candidates)
                    committed_at = _utc_now()
                    retention_until = _format_utc(now + timedelta(days=90))
                    run_id = provisional_run_id
                    if run_id is None:
                        raise RegistryStorageError("correlation_missing")
                    existing_run = connection.execute(
                        "SELECT 1 FROM run_contexts WHERE run_id = ? AND workspace_id = ?",
                        (run_id, workspace_id),
                    ).fetchone()
                    if existing_run is None:
                        raise RegistryStorageError("conflict")
                    connection.execute(
                        "INSERT INTO route_decisions(run_id, route_token_hash, candidate_snapshot_hash, decision_json, committed_at, retention_until) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (run_id, token_hash, candidate_snapshot_hash, _canonical_json(decision), committed_at, retention_until),
                    )
                    connection.execute("DELETE FROM route_offers WHERE route_token_hash = ?", (token_hash,))
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except RegistryStorageError:
            raise
        except sqlite3.Error as error:
            raise RegistryStorageError("internal_error") from error
        return {
            "schema_version": "skilltree/v1",
            "run_id": run_id,
            "selected_skill_name": decision["selected_skill_name"],
            "committed_at": committed_at,
        }

    def find_turn_trace(self, workspace_id: str, session_id: str, turn_id: str | None) -> dict[str, str] | None:
        """Resolve the current persisted TurnTrace without creating or mutating state."""
        if not _is_sha256_value(workspace_id) or not isinstance(session_id, str) or not session_id:
            return None
        try:
            with closing(self._connect()) as connection:
                query = (
                    "SELECT t.turn_trace_id, b.run_id FROM turn_traces AS t "
                    "LEFT JOIN run_turn_bindings AS b ON b.turn_trace_id = t.turn_trace_id "
                    "WHERE t.workspace_id = ? AND t.session_id = ? "
                )
                params: tuple[object, ...] = (workspace_id, session_id)
                if turn_id is not None:
                    query += "AND t.turn_id = ? "
                    params += (turn_id,)
                query += "ORDER BY t.rowid DESC LIMIT 1"
                row = connection.execute(query, params).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        return {"turn_trace_id": row[0], "run_id": row[1]} if row[1] is not None else {"turn_trace_id": row[0], "run_id": ""}

    def trace_reserve(
        self,
        *,
        workspace_id: str,
        session_id: str,
        session_id_hash: str,
        turn_id: str,
        prompt_hash: str,
        route_token: str | None,
    ) -> dict[str, str | None]:
        """Create a Hook-only TurnTrace and atomically bind its current RouteOffer.

        This deliberately has no CLI adapter.  The returned raw turn token is for
        the in-process Hook bridge only; SQLite retains only its SHA-256 hash.
        """
        _validate_turn_trace_input(workspace_id, session_id, session_id_hash, turn_id, prompt_hash, route_token)
        turn_trace_id = str(uuid4())
        turn_token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        soft_expires_at = _format_utc(now + timedelta(seconds=90))
        hard_expires_at = _format_utc(now + timedelta(minutes=5))
        retention_until = _format_utc(now + timedelta(days=7))
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    _require_trace_capture_enabled(connection)
                    connection.execute(
                        "INSERT INTO turn_traces(turn_trace_id, session_id, turn_id, session_id_hash, workspace_id, "
                        "turn_token_hash, soft_expires_at, hard_expires_at, consumed_at, prompt_hash, coverage_state, "
                        "closed_at, retention_until) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'unattributed', NULL, ?)",
                        (
                            turn_trace_id,
                            session_id,
                            turn_id,
                            session_id_hash,
                            workspace_id,
                            _hash_text(turn_token),
                            soft_expires_at,
                            hard_expires_at,
                            prompt_hash,
                            retention_until,
                        ),
                    )
                    if route_token is None:
                        connection.commit()
                        return {
                            "turn_trace_id": turn_trace_id,
                            "turn_token": turn_token,
                            "run_id": None,
                            "bind_state": None,
                        }
                    try:
                        result = self._bind_turn_trace_on_connection(
                            connection,
                            turn_token=turn_token,
                            workspace_id=workspace_id,
                            session_id_hash=session_id_hash,
                            route_token=route_token,
                            now=now,
                        )
                    except RegistryStorageError:
                        # A UserPrompt turn is still observable but remains unattributed.
                        connection.commit()
                        raise
                    connection.commit()
                except Exception:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
        except RegistryStorageError:
            raise
        except sqlite3.IntegrityError as error:
            raise RegistryStorageError("conflict") from error
        except sqlite3.Error as error:
            raise RegistryStorageError("internal_error") from error
        return {
            "turn_trace_id": turn_trace_id,
            "turn_token": turn_token,
            "run_id": result["run_id"],
            "bind_state": result["bind_state"],
        }

    def _bind_turn_trace(
        self,
        *,
        turn_token: str,
        workspace_id: str,
        session_id_hash: str,
        route_token: str,
    ) -> dict[str, str]:
        """Bind an existing Hook-only TurnTrace; kept private to the Core."""
        if (
            not isinstance(turn_token, str)
            or not isinstance(route_token, str)
            or not _is_sha256_value(workspace_id)
            or not _is_sha256_value(session_id_hash)
        ):
            raise RegistryStorageError("invalid_schema")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    _require_trace_capture_enabled(connection)
                    result = self._bind_turn_trace_on_connection(
                        connection,
                        turn_token=turn_token,
                        workspace_id=workspace_id,
                        session_id_hash=session_id_hash,
                        route_token=route_token,
                        now=datetime.now(UTC),
                    )
                    connection.commit()
                    return result
                except Exception:
                    connection.rollback()
                    raise
        except RegistryStorageError:
            raise
        except sqlite3.Error as error:
            raise RegistryStorageError("internal_error") from error

    def maintenance_sweep(self) -> dict[str, object]:
        """Explicitly purge only expired offers and expired unrouted provisional runs."""
        now = datetime.now(UTC)
        now_text = _format_utc(now)
        try:
            with closing(self._connect()) as connection:
                expired_offers = _first_hundred_per_workspace(
                    list(
                        connection.execute(
                            "SELECT route_token_hash, workspace_id FROM route_offers "
                            "WHERE expires_at <= ? ORDER BY workspace_id ASC, expires_at ASC, route_token_hash ASC",
                            (now_text,),
                        )
                    )
                )
            deleted_offers = sum(
                self._delete_expired_offer(route_token_hash, now_text)
                for route_token_hash, _ in expired_offers
            )
            with closing(self._connect()) as connection:
                expired_runs = _first_hundred_per_workspace(
                    list(
                        connection.execute(
                            "SELECT r.run_id, r.workspace_id FROM run_contexts AS r "
                            "JOIN run_turn_bindings AS b ON b.run_id = r.run_id "
                            "JOIN turn_traces AS t ON t.turn_trace_id = b.turn_trace_id "
                            "LEFT JOIN route_decisions AS d ON d.run_id = r.run_id "
                            "WHERE d.run_id IS NULL AND r.retention_until <= ? AND t.retention_until <= ? "
                            "ORDER BY r.workspace_id ASC, r.retention_until ASC, r.run_id ASC",
                            (now_text, now_text),
                        )
                    )
                )
            deleted_runs = sum(
                self._delete_expired_unrouted_run(run_id, now_text, now)
                for run_id, _ in expired_runs
            )
        except sqlite3.Error as error:
            raise RegistryStorageError("internal_error") from error
        return {
            "expired_offers_deleted": deleted_offers,
            "unrouted_runs_deleted": deleted_runs,
            "completed_at": now_text,
        }

    def _delete_expired_offer(self, route_token_hash: str, now_text: str) -> int:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                deleted = connection.execute(
                    "DELETE FROM route_offers WHERE route_token_hash = ? AND expires_at <= ?",
                    (route_token_hash, now_text),
                ).rowcount
                connection.commit()
                return deleted
            except Exception:
                connection.rollback()
                raise

    def _delete_expired_unrouted_run(self, run_id: str, now_text: str, now: datetime) -> int:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                candidate = connection.execute(
                    "SELECT r.workspace_id, t.turn_trace_id FROM run_contexts AS r "
                    "JOIN run_turn_bindings AS b ON b.run_id = r.run_id "
                    "JOIN turn_traces AS t ON t.turn_trace_id = b.turn_trace_id "
                    "LEFT JOIN route_decisions AS d ON d.run_id = r.run_id "
                    "WHERE r.run_id = ? AND d.run_id IS NULL AND r.retention_until <= ? AND t.retention_until <= ?",
                    (run_id, now_text, now_text),
                ).fetchone()
                if candidate is None:
                    connection.commit()
                    return 0
                workspace_id, turn_trace_id = candidate
                connection.execute("DELETE FROM turn_traces WHERE turn_trace_id = ?", (turn_trace_id,))
                deleted = connection.execute(
                    "DELETE FROM run_contexts WHERE run_id = ? AND NOT EXISTS "
                    "(SELECT 1 FROM route_decisions WHERE route_decisions.run_id = ?)",
                    (run_id, run_id),
                ).rowcount
                if deleted:
                    try:
                        _write_workspace_audit(
                            connection,
                            workspace_id=workspace_id,
                            event_type="unrouted_trace_purged",
                            object_handle_hash=_hash_text(f"unrouted_run/{run_id}"),
                            reason_code="retention_expired",
                            policy_version="maintenance/v1",
                            now=now,
                        )
                    except sqlite3.Error:
                        # Audit retention must not retain the user's expired trace data.
                        pass
                connection.commit()
                return deleted
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _bind_turn_trace_on_connection(
        connection: sqlite3.Connection,
        *,
        turn_token: str,
        workspace_id: str,
        session_id_hash: str,
        route_token: str,
        now: datetime,
    ) -> dict[str, str]:
        trace = connection.execute(
            "SELECT turn_trace_id, workspace_id, session_id_hash, soft_expires_at, hard_expires_at, consumed_at, closed_at "
            "FROM turn_traces WHERE turn_token_hash = ?",
            (_hash_text(turn_token),),
        ).fetchone()
        if trace is None:
            raise RegistryStorageError("correlation_missing")
        turn_trace_id, trace_workspace_id, trace_session_id_hash, soft_expires_at, hard_expires_at, consumed_at, closed_at = trace
        if trace_workspace_id != workspace_id or trace_session_id_hash != session_id_hash:
            raise RegistryStorageError("correlation_missing")
        if consumed_at is not None or closed_at is not None:
            raise RegistryStorageError("conflict")
        if _parse_utc(hard_expires_at) <= now:
            raise RegistryStorageError("correlation_missing")

        offer = connection.execute(
            "SELECT workspace_id, session_id_hash, provisional_run_id, trusted_snapshot_json, expires_at "
            "FROM route_offers WHERE route_token_hash = ?",
            (_hash_text(route_token),),
        ).fetchone()
        if offer is None:
            raise RegistryStorageError("correlation_missing")
        offer_workspace_id, offer_session_id_hash, provisional_run_id, trusted_snapshot_json, expires_at = offer
        if offer_workspace_id != workspace_id or offer_session_id_hash != session_id_hash:
            raise RegistryStorageError("correlation_missing")
        if _parse_utc(expires_at) <= now:
            raise RegistryStorageError("correlation_missing")
        if provisional_run_id is not None:
            raise RegistryStorageError("conflict")

        created_at = _format_utc(now)
        retention_until = _format_utc(now + timedelta(days=7))
        settings = connection.execute(
            "SELECT trace_capture_enabled, memory_read_enabled, memory_write_enabled, replay_capture_enabled "
            "FROM runtime_config WHERE config_id = 1"
        ).fetchone()
        if settings is None:
            raise RegistryStorageError("internal_error")
        run_id = str(uuid4())
        connection.execute(
            "INSERT INTO run_contexts(run_id, workspace_id, user_id, snapshot_json, trace_capture_enabled, "
            "memory_read_enabled, memory_write_enabled, replay_capture_enabled, created_at, retention_until) "
            "VALUES (?, ?, 'local', ?, ?, ?, ?, ?, ?, ?)",
            (run_id, workspace_id, trusted_snapshot_json, *settings, created_at, retention_until),
        )
        bind_state = "late" if _parse_utc(soft_expires_at) <= now else "normal"
        connection.execute(
            "INSERT INTO run_turn_bindings(run_id, turn_trace_id, bound_at, bind_state) VALUES (?, ?, ?, ?)",
            (run_id, turn_trace_id, created_at, bind_state),
        )
        connection.execute(
            "UPDATE turn_traces SET consumed_at = ? WHERE turn_trace_id = ? AND consumed_at IS NULL",
            (created_at, turn_trace_id),
        )
        updated = connection.execute(
            "UPDATE route_offers SET provisional_run_id = ? WHERE route_token_hash = ? AND provisional_run_id IS NULL",
            (run_id, _hash_text(route_token)),
        ).rowcount
        if updated != 1:
            raise RegistryStorageError("conflict")
        return {"run_id": run_id, "bind_state": bind_state}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL").fetchone()
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _applied_hashes(connection: sqlite3.Connection) -> dict[int, str]:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if exists is None:
            return {}
        return dict(connection.execute("SELECT version, content_hash FROM schema_migrations"))


def _execute_statements(connection: sqlite3.Connection, sql: str) -> None:
    statements = [statement.strip() for statement in sql.split(";") if statement.strip()]
    if not statements:
        raise StorageInitializationError("migration is empty")
    for statement in statements:
        connection.execute(statement)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _configured_root(connection: sqlite3.Connection) -> Path | None:
    row = connection.execute("SELECT skill_root FROM runtime_config WHERE config_id = 1").fetchone()
    return Path(row[0]) if row is not None and row[0] is not None else None


def _registry_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts = dict(connection.execute("SELECT state, COUNT(*) FROM skills GROUP BY state"))
    return {
        "pending_count": counts.get("pending", 0),
        "trusted_count": counts.get("trusted", 0),
        "invalid_count": counts.get("invalid", 0),
        "out_of_scope_count": counts.get("out_of_scope", 0),
    }


def _write_audit(
    connection: sqlite3.Connection,
    event_type: str,
    object_handle_hash: str,
    reason_code: str,
    policy_version: str,
) -> None:
    now = datetime.now(UTC)
    connection.execute(
        "INSERT INTO audit_events(audit_id, scope, workspace_id, event_type, object_handle_hash, reason_code, "
        "policy_version, created_at, retention_until) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid4()),
            "user_global",
            event_type,
            object_handle_hash,
            reason_code,
            policy_version,
            now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            (now + timedelta(days=30)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        ),
    )


def _write_workspace_audit(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    event_type: str,
    object_handle_hash: str,
    reason_code: str,
    policy_version: str,
    now: datetime,
) -> None:
    connection.execute(
        "INSERT INTO audit_events(audit_id, scope, workspace_id, event_type, object_handle_hash, reason_code, "
        "policy_version, created_at, retention_until) VALUES (?, 'workspace', ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid4()),
            workspace_id,
            event_type,
            object_handle_hash,
            reason_code,
            policy_version,
            _format_utc(now),
            _format_utc(now + timedelta(days=30)),
        ),
    )


def _first_hundred_per_workspace(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    selected: list[tuple[str, str]] = []
    counts: dict[str, int] = {}
    for object_id, workspace_id in rows:
        count = counts.get(workspace_id, 0)
        if count >= 100:
            continue
        counts[workspace_id] = count + 1
        selected.append((object_id, workspace_id))
    return selected


def _hash_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256_value(value: str) -> bool:
    return len(value) == 71 and value.startswith("sha256:") and all(character in "0123456789abcdef" for character in value[7:])


def _validate_turn_trace_input(
    workspace_id: object,
    session_id: object,
    session_id_hash: object,
    turn_id: object,
    prompt_hash: object,
    route_token: object,
) -> None:
    if (
        not isinstance(workspace_id, str)
        or not _is_sha256_value(workspace_id)
        or not isinstance(session_id_hash, str)
        or not _is_sha256_value(session_id_hash)
        or not isinstance(prompt_hash, str)
        or not _is_sha256_value(prompt_hash)
        or not isinstance(session_id, str)
        or not session_id
        or len(session_id) > 256
        or not isinstance(turn_id, str)
        or not turn_id
        or len(turn_id) > 256
        or (route_token is not None and (not isinstance(route_token, str) or not route_token))
    ):
        raise RegistryStorageError("invalid_schema")


def _require_trace_capture_enabled(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT trace_capture_enabled FROM runtime_config WHERE config_id = 1"
    ).fetchone()
    if row is None:
        raise RegistryStorageError("internal_error")
    if not bool(row[0]):
        raise RegistryStorageError("authorization_required")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _route_candidates(
    rows: list[dict[str, str]], prompt: str
) -> tuple[list[dict[str, str]], bool]:
    catalog = build_metadata_catalog(rows)
    if not catalog.degraded:
        return catalog.candidates, False
    return lexical_top_k(rows, prompt), True


def _format_utc(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise RegistryStorageError("conflict") from error
    if parsed.tzinfo is None:
        raise RegistryStorageError("conflict")
    return parsed.astimezone(UTC)


def _validate_route_decision(decision: dict[str, object], candidates: object) -> None:
    """Validate the complete P2 RouteDecision against its exact offered candidates."""
    if not isinstance(decision, dict) or set(decision) != {
        "schema_version", "intent", "constraints", "ranked_candidates", "selected_skill_name", "ordered_skill_names", "degraded"
    }:
        raise RegistryStorageError("invalid_schema")
    if decision["schema_version"] != "skilltree/v1" or type(decision["degraded"]) is not bool:
        raise RegistryStorageError("invalid_schema")
    intent = decision["intent"]
    if not isinstance(intent, dict) or set(intent) != {"name", "confidence"}:
        raise RegistryStorageError("invalid_schema")
    intent_name = intent["name"]
    confidence = intent["confidence"]
    if (
        not isinstance(intent_name, str)
        or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", intent_name) is None
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0.0 <= confidence <= 1.0
    ):
        raise RegistryStorageError("invalid_schema")
    constraints = decision["constraints"]
    if not isinstance(constraints, list) or len(constraints) > 12 or any(
        not isinstance(value, str) or not value or len(value) > 64 for value in constraints
    ):
        raise RegistryStorageError("invalid_schema")
    if not isinstance(candidates, list):
        raise RegistryStorageError("conflict")
    offered_names = {item.get("name") for item in candidates if isinstance(item, dict)}
    if len(offered_names) != len(candidates) or any(not isinstance(name, str) for name in offered_names):
        raise RegistryStorageError("conflict")
    ranked = decision["ranked_candidates"]
    if not isinstance(ranked, list) or not 1 <= len(ranked) <= min(8, len(offered_names)):
        raise RegistryStorageError("invalid_schema")
    ranked_names: list[str] = []
    for expected_rank, candidate in enumerate(ranked, start=1):
        if not isinstance(candidate, dict) or set(candidate) != {"name", "rank", "reason"}:
            raise RegistryStorageError("invalid_schema")
        name, rank, reason = candidate["name"], candidate["rank"], candidate["reason"]
        if not isinstance(name, str) or name not in offered_names or type(rank) is not int or rank != expected_rank or not isinstance(reason, str) or not reason or len(reason) > 500:
            raise RegistryStorageError("invalid_schema")
        ranked_names.append(name)
    if len(set(ranked_names)) != len(ranked_names):
        raise RegistryStorageError("invalid_schema")
    selected = decision["selected_skill_name"]
    ordered = decision["ordered_skill_names"]
    if not isinstance(selected, str) or selected not in offered_names or selected not in ranked_names or not isinstance(ordered, list) or not 1 <= len(ordered) <= 3 or any(not isinstance(name, str) or name not in offered_names for name in ordered) or len(set(ordered)) != len(ordered) or ordered[0] != selected:
        raise RegistryStorageError("invalid_schema")


def _normalize_route_decision(decision: dict[str, object], candidates: object) -> dict[str, object]:
    """Expand a compact router summary into the persisted RouteDecision shape."""
    if not isinstance(decision, dict) or not isinstance(candidates, list):
        raise RegistryStorageError("invalid_schema")
    full_shape = {"schema_version", "intent", "constraints", "ranked_candidates", "selected_skill_name", "ordered_skill_names", "degraded"}
    if set(decision) == full_shape:
        return decision
    allowed = {"schema_version", "selected_skill_name", "ordered_skill_names", "confidence", "degraded"}
    if not set(decision).issubset(allowed) or "selected_skill_name" not in decision:
        raise RegistryStorageError("invalid_schema")
    if "schema_version" in decision and decision["schema_version"] != "skilltree/v1":
        raise RegistryStorageError("invalid_schema")
    offered_names = [item.get("name") for item in candidates if isinstance(item, dict)]
    if len(offered_names) != len(candidates) or any(not isinstance(name, str) for name in offered_names):
        raise RegistryStorageError("conflict")
    selected = decision["selected_skill_name"]
    if not isinstance(selected, str) or selected not in offered_names:
        raise RegistryStorageError("invalid_schema")
    ordered_value = decision.get("ordered_skill_names", [selected])
    if not isinstance(ordered_value, list) or not 1 <= len(ordered_value) <= 3 or any(not isinstance(name, str) or name not in offered_names for name in ordered_value) or len(set(ordered_value)) != len(ordered_value) or ordered_value[0] != selected:
        raise RegistryStorageError("invalid_schema")
    confidence = decision.get("confidence", 0.0)
    degraded = decision.get("degraded", True)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0 or type(degraded) is not bool:
        raise RegistryStorageError("invalid_schema")
    return {"schema_version": "skilltree/v1", "intent": {"name": "skill_routing", "confidence": float(confidence)}, "constraints": [], "ranked_candidates": [{"name": name, "rank": rank, "reason": "legacy compact route summary"} for rank, name in enumerate(ordered_value, start=1)], "selected_skill_name": selected, "ordered_skill_names": ordered_value, "degraded": degraded}


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _stored_path_is_within_root(path: str, root: Path) -> bool:
    try:
        return _is_within_root(Path(path).resolve(strict=False), root)
    except OSError:
        return False
