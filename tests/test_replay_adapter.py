from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "extensions" / "replay-runner"))

from adapters.base import TaskRequest  # noqa: E402
from adapters.python_repository import PythonRepositoryAdapter  # noqa: E402


def test_python_repository_adapter_verifies_binary_tree_source(tmp_path: Path) -> None:
    (tmp_path / "lesson.py").write_text(
        "class Solution:\n"
        "    def diameterOfBinaryTree(self, root):\n"
        "        self.res = 0\n"
        "        self.backtrack(root)\n"
        "        return self.res\n"
        "    def backtrack(self, root):\n"
        "        if root is None: return 0\n"
        "        left = self.backtrack(root.left)\n"
        "        right = self.backtrack(root.right)\n"
        "        self.res = max(self.res, left + right)\n"
        "        return max(left, right) + 1\n",
        encoding="utf-8",
    )
    result = PythonRepositoryAdapter(tmp_path).run(TaskRequest("e", "candidate", "repository_verification", {"source_name": "lesson.py", "verification": "binary_tree_diameter"}, {}))
    assert result.verdict == "success"
    assert result.quality_score == 1.0
