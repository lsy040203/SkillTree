from __future__ import annotations

import importlib.util
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .base import AdapterResult, TaskRequest


_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+\.py$")


class PythonRepositoryAdapter:
    task_type = "org.skilltree.python.repository_verification"
    legacy_task_type = "repository_verification"

    def __init__(self, input_root: Path) -> None:
        self.input_root = input_root

    def run(self, request: TaskRequest) -> AdapterResult:
        started = time.monotonic()
        fixture = request.fixture
        source_name = fixture.get("source_name", "lesson01_practice.py")
        verification = fixture.get("verification", "binary_tree_diameter")
        if not isinstance(source_name, str) or not _SAFE_NAME.fullmatch(source_name):
            return self._failed(started, "invalid_fixture")
        if verification != "binary_tree_diameter":
            return self._failed(started, "unsupported_verification")
        source = self.input_root / source_name
        if source.parent != self.input_root or not source.is_file():
            return self._failed(started, "source_missing")

        # Fixed Bash read step; callers cannot supply a command or path.  The
        # runner normally uses /input inside the OCI container, while unit
        # tests may provide a temporary host directory.
        if self.input_root == Path("/input"):
            quoted_source = shlex.quote("/input/" + source_name)
            bash = subprocess.run(
                ["bash", "-lc", f"test -r {quoted_source} && sed -n '1,240p' {quoted_source}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if bash.returncode != 0 or not bash.stdout.strip():
                return self._failed(started, "source_unreadable")
        else:
            # Host-side unit tests cannot address the container's /input mount;
            # retain the same bounded read semantics without invoking WSL.
            try:
                if not source.read_text(encoding="utf-8").strip():
                    return self._failed(started, "source_unreadable")
            except (OSError, UnicodeError):
                return self._failed(started, "source_unreadable")
        try:
            module = _load_module(source)
            tree_node = type("TreeNode", (), {})
            root = tree_node(); root.val = 1; root.left = tree_node(); root.right = tree_node()
            root.left.val = 2; root.left.left = tree_node(); root.left.left.val = 4; root.left.right = tree_node(); root.left.right.val = 5
            root.right.val = 3; root.right.left = None; root.right.right = None
            root.left.left.left = None; root.left.left.right = None; root.left.right.left = None; root.left.right.right = None
            result = module.Solution().diameterOfBinaryTree(root)
        except Exception:
            return self._failed(started, "python_verification_failed")
        expected = fixture.get("expected_diameter", 3)
        if result != expected:
            return self._failed(started, "assertion_failed")
        return AdapterResult("success", 1.0, _elapsed_ms(started), [])

    @staticmethod
    def _failed(started: float, reason: str) -> AdapterResult:
        return AdapterResult("failed", 0.0, _elapsed_ms(started), [], reason)


def _load_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("replay_task", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module_load_failed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _elapsed_ms(started: float) -> int:
    return max(1, int((time.monotonic() - started) * 1000))
