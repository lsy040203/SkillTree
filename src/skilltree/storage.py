from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path


DEFAULT_RUNTIME_SETTINGS = {
    "trace_capture_enabled": False,
    "memory_read_enabled": False,
    "memory_write_enabled": False,
    "replay_capture_enabled": False,
}


MIGRATIONS = {
    1: """
    CREATE TABLE runtime_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE audit_events (
        audit_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        object_handle TEXT,
        reason_code TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, content_hash TEXT NOT NULL)"
            )
            applied_versions = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, migration in MIGRATIONS.items():
                if version in applied_versions:
                    continue
                content_hash = hashlib.sha256(migration.encode("utf-8")).hexdigest()
                with connection:
                    connection.executescript(migration)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at, content_hash) "
                        "VALUES (?, datetime('now'), ?)",
                        (version, content_hash),
                    )
            with connection:
                for key, value in DEFAULT_RUNTIME_SETTINGS.items():
                    connection.execute(
                        "INSERT OR IGNORE INTO runtime_settings(key, value) VALUES (?, ?)",
                        (key, "true" if value else "false"),
                    )

    def applied_migrations(self) -> list[int]:
        with closing(self._connect()) as connection:
            return [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]

    def read_runtime_settings(self) -> dict[str, bool]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT key, value FROM runtime_settings").fetchall()
        return {key: value == "true" for key, value in rows if key in DEFAULT_RUNTIME_SETTINGS}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
