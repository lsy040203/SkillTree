"""ReplayCapsule creation, encrypted blob lifecycle, and expiry cleanup."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from skilltree.core import secret_protector
from skilltree.core.sanitize import contains_secret_pattern
from skilltree.core.storage import Database, RegistryStorageError


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ReplayCapsuleError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def create_replay_capsule(
    database: Database,
    *,
    data_dir: Path,
    run_id: str,
    consent_id: str,
    fixture: dict[str, object],
    expires_at: str | None = None,
) -> dict[str, object]:
    """Create one ready Capsule or a rejected metadata-only row."""
    _uuid(run_id)
    _uuid(consent_id)
    now = _now_dt()
    expiry = expires_at or _format(now + timedelta(days=30))
    capsule_id = str(uuid4())
    data_dir = data_dir.expanduser().resolve()
    try:
        with closing(database._connect()) as connection:
            run = connection.execute(
                "SELECT workspace_id,replay_capture_enabled FROM run_contexts WHERE run_id=?", (run_id,)
            ).fetchone()
            episode = connection.execute(
                "SELECT episode_id,turn_trace_id,snapshot_partial,trace_state,coverage_state,trusted_skill_snapshot "
                "FROM episodes WHERE run_id=?", (run_id,)
            ).fetchone()
            rejection = _eligibility_error(run, episode, fixture)
            if rejection:
                connection.execute(
                    "INSERT INTO replay_capsules(replay_capsule_id,run_id,workspace_id,mode,status,retention_until,created_at) VALUES (?,?,?,?,?,?,?)",
                    (capsule_id, run_id, run[0] if run else "unknown", "fixture_only", "rejected", _format(now + timedelta(days=90)), _format(now)),
                )
                if episode:
                    connection.execute("UPDATE episodes SET snapshot_partial=1 WHERE episode_id=?", (episode[0],))
                connection.commit()
                return {"replay_capsule_id": capsule_id, "status": "rejected", "reason": rejection}
            content = _canonical_fixture(fixture, episode[5])
            encrypted = secret_protector.protect(content)
            blob_dir = data_dir / "replay-blobs"
            blob_dir.mkdir(parents=True, exist_ok=True)
            blob_name = f"{capsule_id}.blob"
            blob_path = blob_dir / blob_name
            _exclusive_write(blob_path, encrypted)
            content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
            try:
                connection.execute(
                    "INSERT INTO replay_capsules(replay_capsule_id,run_id,workspace_id,mode,consent_id,blob_handle,content_hash,status,expires_at,retention_until,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (capsule_id, run_id, run[0], "fixture_only", consent_id, blob_name, content_hash, "ready", expiry, _format(now + timedelta(days=90)), _format(now)),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                blob_path.unlink(missing_ok=True)
                raise
    except secret_protector.SecretProtectionError as error:
        raise ReplayCapsuleError(error.args[0]) from error
    except OSError as error:
        raise ReplayCapsuleError("internal_error") from error
    return {"replay_capsule_id": capsule_id, "status": "ready", "blob_handle": blob_name, "content_hash": content_hash, "expires_at": expiry}


def read_replay_capsule(data_dir: Path, capsule_id: str) -> dict[str, object]:
    _uuid(capsule_id)
    path = data_dir.expanduser().resolve() / "replay-blobs" / f"{capsule_id}.blob"
    try:
        return json.loads(secret_protector.unprotect(path.read_bytes()).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, secret_protector.SecretProtectionError) as error:
        raise ReplayCapsuleError("not_found") from error


def delete_replay_capsule(database: Database, *, data_dir: Path, capsule_id: str, status: str = "deleted") -> dict[str, object]:
    _uuid(capsule_id)
    if status not in {"deleted", "expired"}:
        raise ReplayCapsuleError("invalid_schema")
    data_dir = data_dir.expanduser().resolve()
    with closing(database._connect()) as connection:
        row = connection.execute("SELECT run_id,blob_handle FROM replay_capsules WHERE replay_capsule_id=?", (capsule_id,)).fetchone()
        if row is None:
            raise ReplayCapsuleError("not_found")
        if row[1]:
            (data_dir / "replay-blobs" / row[1]).unlink(missing_ok=True)
        connection.execute("UPDATE replay_capsules SET consent_id=NULL,blob_handle=NULL,content_hash=NULL,expires_at=NULL,status=? WHERE replay_capsule_id=?", (status, capsule_id))
        connection.commit()
    return {"replay_capsule_id": capsule_id, "status": status}


def sweep_replay_capsules(database: Database, *, data_dir: Path, now: datetime | None = None) -> dict[str, int]:
    now = now or _now_dt()
    with closing(database._connect()) as connection:
        try:
            rows = connection.execute("SELECT replay_capsule_id FROM replay_capsules WHERE status='ready' AND expires_at<=?", (_format(now),)).fetchall()
        except sqlite3.OperationalError as error:
            if "no such table: replay_capsules" not in str(error):
                raise
            return {}
    expired = 0
    for row in rows:
        delete_replay_capsule(database, data_dir=data_dir, capsule_id=row[0], status="expired")
        expired += 1
    return {"replay_capsules_expired": expired}


def _eligibility_error(run: tuple[object, ...] | None, episode: tuple[object, ...] | None, fixture: object) -> str | None:
    if run is None or not bool(run[1]):
        return "authorization_required"
    if episode is None:
        return "episode_missing"
    if bool(episode[2]) or episode[3] != "complete" or episode[4] != "observed":
        return "episode_incomplete"
    if not isinstance(fixture, dict) or not fixture:
        return "fixture_missing"
    if _contains_rejected(fixture):
        return "fixture_rejected"
    return None


def _canonical_fixture(fixture: dict[str, object], skill_snapshot: str) -> bytes:
    safe = {"schema_version": "skilltree-replay-capsule/v1", "fixture": fixture, "skill_snapshot": skill_snapshot}
    return (json.dumps(safe, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _contains_rejected(value: object) -> bool:
    if isinstance(value, str):
        # Replay fixtures may legitimately contain multiline source code. Do
        # not apply the description sanitizer's control-character rule here;
        # reject only credential-like patterns.
        return contains_secret_pattern(value)
    if isinstance(value, dict):
        return any(_contains_rejected(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_rejected(item) for item in value)
    return False


def _exclusive_write(path: Path, value: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _uuid(value: str) -> None:
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise ReplayCapsuleError("invalid_schema") from None


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _format(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
