from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class SkillRootError(ValueError):
    """Raised when a skill root is not explicitly confirmed and safe."""


@dataclass
class RuntimeConfig:
    data_dir: Path
    skill_root: Path | None = None
    trace_capture_enabled: bool = False
    memory_read_enabled: bool = False
    memory_write_enabled: bool = False
    replay_capture_enabled: bool = False

    @property
    def path(self) -> Path:
        return self.data_dir / "config.json"

    @classmethod
    def load(cls, data_dir: Path) -> "RuntimeConfig":
        normalized_data_dir = data_dir.expanduser().resolve()
        path = normalized_data_dir / "config.json"
        if not path.is_file():
            return cls(data_dir=normalized_data_dir)

        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        skill_root_value = raw.get("skill_root")
        return cls(
            data_dir=normalized_data_dir,
            skill_root=Path(skill_root_value) if skill_root_value else None,
            trace_capture_enabled=bool(raw.get("trace_capture_enabled", False)),
            memory_read_enabled=bool(raw.get("memory_read_enabled", False)),
            memory_write_enabled=bool(raw.get("memory_write_enabled", False)),
            replay_capture_enabled=bool(raw.get("replay_capture_enabled", False)),
        )

    def set_skill_root(self, path: Path, *, confirmed: bool) -> None:
        if not confirmed:
            raise SkillRootError("skill_root requires explicit user confirmation")
        self.skill_root = self._validate_skill_root(path)
        self.save()

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload["data_dir"] = str(self.data_dir)
        payload["skill_root"] = str(self.skill_root) if self.skill_root else None
        temporary_path = self.path.with_suffix(".tmp")
        with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, self.path)

    @staticmethod
    def _validate_skill_root(path: Path) -> Path:
        raw_path = str(path)
        if raw_path.startswith("\\\\"):
            raise SkillRootError("network share skill_root is not supported")
        if not path.is_absolute():
            raise SkillRootError("skill_root must be an absolute local path")
        normalized = path.expanduser().resolve(strict=False)
        if not normalized.exists() or not normalized.is_dir():
            raise SkillRootError("skill_root must be an existing directory")
        return normalized
