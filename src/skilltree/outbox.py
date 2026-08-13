from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable


class AtomicOutbox:
    def __init__(self, root: Path) -> None:
        self.root = root

    def enqueue(self, payload: dict[str, Any]) -> Path:
        staging_dir = self.root / "staging"
        ready_dir = self.root / "ready"
        staging_dir.mkdir(parents=True, exist_ok=True)
        ready_dir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        temporary_path = staging_dir / f"{token}.tmp"
        ready_path = ready_dir / f"{token}.json"
        with temporary_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, ready_path)
        return ready_path


class WriterLease:
    def __init__(
        self,
        path: Path,
        *,
        owner_id: str,
        ttl_seconds: int,
        health_check: Callable[[], bool] | None = None,
    ) -> None:
        self.path = path
        self.owner_id = owner_id
        self.ttl_seconds = ttl_seconds
        self.health_check = health_check or (lambda: True)

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            if not self._expired() or not self.health_check():
                return False
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

        payload = {
            "owner_id": self.owner_id,
            "pid": os.getpid(),
            "expires_at": time.time() + self.ttl_seconds,
        }
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        return True

    def release(self) -> None:
        try:
            lease = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if lease.get("owner_id") == self.owner_id:
            self.path.unlink(missing_ok=True)

    def _expired(self) -> bool:
        try:
            lease = json.loads(self.path.read_text(encoding="utf-8"))
            return float(lease.get("expires_at", 0)) <= time.time()
        except (OSError, ValueError, json.JSONDecodeError):
            return False
