from __future__ import annotations

from pathlib import Path

from .base import TaskAdapter
from .python_repository import PythonRepositoryAdapter


def build_registry(input_root: Path) -> dict[str, TaskAdapter]:
    adapter = PythonRepositoryAdapter(input_root)
    return {adapter.task_type: adapter, adapter.legacy_task_type: adapter}
