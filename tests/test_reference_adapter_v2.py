from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "extensions" / "replay-runner"
sys.path.insert(0, str(ROOT))

from adapters.base import TaskRequest  # noqa: E402
from adapters.python_repository import PythonRepositoryAdapter  # noqa: E402
from adapters.registry import build_registry  # noqa: E402


def test_reference_adapter_declares_namespaced_type_and_legacy_alias(tmp_path: Path) -> None:
    adapter = PythonRepositoryAdapter(tmp_path)
    assert adapter.task_type == "org.skilltree.python.repository_verification"
    assert adapter.legacy_task_type == "repository_verification"
    assert set(build_registry(tmp_path)) == {adapter.task_type, adapter.legacy_task_type}


def test_reference_adapter_runs_binary_tree_fixture(tmp_path: Path) -> None:
    source = tmp_path / "lesson.py"
    source.write_text("class Solution:\n    def diameterOfBinaryTree(self, root):\n        self.res=0\n        def f(n):\n            if not n: return 0\n            l=f(n.left); r=f(n.right); self.res=max(self.res,l+r); return max(l,r)+1\n        f(root); return self.res\n", encoding="utf-8")
    result = PythonRepositoryAdapter(tmp_path).run(TaskRequest("episode", "baseline", "org.skilltree.python.repository_verification", {"source_name": "lesson.py", "verification": "binary_tree_diameter"}, {}))
    assert result.verdict == "success"
