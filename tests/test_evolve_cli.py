from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_evolve_scan_is_explicit_and_fail_closed_without_extension(tmp_path: Path) -> None:
    request = tmp_path / "evolve.json"
    request.write_text(json.dumps({"schema_version": "skilltree/v1", "user_id": "local", "workspace_id": "w", "candidate_id": "c", "episode_ids": ["e"], "confirm": "RUN_EVOLVE_SCAN"}), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "skilltree", "evolve", "scan", "--data-dir", str(tmp_path / "data"), "--input", str(request)], capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "replay_runtime_unavailable"
