from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from skilltree.hook_bridge import (
    build_probe,
    handle_hook_event,
    parse_stop_route_commit,
    parse_hook_input,
    write_probe,
)
from skilltree.hooks.hook_bridge import HookInput, summarize_tool_operation


ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / "plugins" / "skilltree" / "runtime" / "skilltree_hook.py"


def test_tool_operation_summary_keeps_only_bounded_categories() -> None:
    event = HookInput(
        event_name="PreToolUse",
        fields={
            "turn_id": "turn-ref",
            "session_id": "session-ref",
            "cwd": "workspace-ref",
            "tool_name": "Bash",
            "tool_use_id": "tool-ref",
            "tool_input": {
                "command": (
                    "Get-ChildItem D:\\Users\\vscode\\lessons; "
                    "Get-Content D:\\Users\\vscode\\lessons\\lesson01_practice.py; "
                    "pytest -q tests/test_memory_extractors.py"
                )
            },
        },
    )

    summary = summarize_tool_operation(event)

    assert summary == "PreToolUse:Bash:inspect_files,read_source,run_tests"
    assert "lesson01_practice.py" not in summary
    assert "D:\\Users" not in summary


def test_tool_operation_summary_classifies_search_ast_lsp_and_python_paths() -> None:
    event = HookInput(
        event_name="PreToolUse",
        fields={
            "turn_id": "turn-ref",
            "session_id": "session-ref",
            "cwd": "workspace-ref",
            "tool_name": "Bash",
            "tool_use_id": "tool-ref",
            "tool_input": {
                "command": (
                    "rg -n 'diameterOfBinaryTree' lessons; "
                    "sg --pattern 'def $F(...):' lessons/lesson01_practice.py; "
                    "& .venv\\Scripts\\python.exe -m pytest -q; "
                    "pyright lessons/lesson01_practice.py; "
                    "python -c 'import matplotlib.pyplot as plt'"
                )
            },
        },
    )

    summary = summarize_tool_operation(event)

    assert summary == (
        "PreToolUse:Bash:search_source,ast_search,run_tests,language_service,"
        "run_python,visualization"
    )


def test_tool_operation_summary_classifies_powershell_python_variable_and_absolute_path() -> None:
    event = HookInput(
        event_name="PreToolUse",
        fields={
            "turn_id": "turn-ref",
            "session_id": "session-ref",
            "cwd": "workspace-ref",
            "tool_name": "Bash",
            "tool_use_id": "tool-ref",
            "tool_input": {
                "command": (
                    '$py = "C:\\Users\\Lenovo\\.codex\\plugins\\data\\venv\\Scripts\\python.exe"; '
                    '& $py -m pytest -q tests/test_memory_extractors.py; '
                    '& $py -c "from lesson01_practice import Solution"'
                )
            },
        },
    )

    assert summarize_tool_operation(event) == "PreToolUse:Bash:run_tests,run_python"


def test_tool_operation_summary_classifies_host_tools_without_shell_commands() -> None:
    event = HookInput(
        event_name="PreToolUse",
        fields={
            "turn_id": "turn-ref",
            "session_id": "session-ref",
            "cwd": "workspace-ref",
            "tool_name": "lsp",
            "tool_use_id": "tool-ref",
            "tool_input": {"file": "lesson01_practice.py"},
        },
    )

    assert summarize_tool_operation(event) == "PreToolUse:lsp:language_service"


def test_stop_parser_accepts_compact_fallback_marker_only_with_turn_id() -> None:
    message = '<!-- skilltree-route-decision:{"schema_version":"skilltree-route-commit/v1","decision":{"selected_skill_name":"analyze"}} -->'

    parsed = parse_stop_route_commit(message, "workspace-ref", "session-ref", "turn-ref")

    assert parsed is not None
    assert parsed["route_token"] is None
    assert parsed["turn_id"] == "turn-ref"


def test_stop_parser_rejects_compact_fallback_marker_without_turn_id() -> None:
    message = '<!-- skilltree-route-decision:{"schema_version":"skilltree-route-commit/v1","decision":{"selected_skill_name":"analyze"}} -->'

    assert parse_stop_route_commit(message, "workspace-ref", "session-ref") is None


def test_stop_parser_accepts_the_official_nullable_last_assistant_message_shape() -> None:
    raw = json.dumps({
        "session_id": "session-ref",
        "transcript_path": None,
        "cwd": "workspace-ref",
        "hook_event_name": "Stop",
        "model": "gpt-test",
        "permission_mode": "default",
        "turn_id": "turn-ref",
        "stop_hook_active": False,
        "last_assistant_message": None,
    }).encode("utf-8")

    event = parse_hook_input("Stop", raw)

    assert event.event_name == "Stop"
    assert event.fields["turn_id"] == "turn-ref"
    assert event.fields["last_assistant_message"] is None


class FakeDatabase:
    def __init__(self, *, trace_capture_enabled: bool = True) -> None:
        self.trace_capture_enabled = trace_capture_enabled
        self.prepared: list[tuple[str, str, str]] = []
        self.reserved: list[dict[str, object]] = []
        self.committed: list[tuple[str, str, str, dict[str, object]]] = []

    def read_runtime_settings(self) -> dict[str, bool]:
        return {"trace_capture_enabled": self.trace_capture_enabled}

    def prepare_route(self, workspace_id: str, session_id_hash: str, prompt: str) -> dict[str, object]:
        self.prepared.append((workspace_id, session_id_hash, prompt))
        return {
            "schema_version": "skilltree-route-envelope/v1",
            "route_token": "a" * 43,
            "expires_at": "2026-08-15T00:05:00.000Z",
            "candidate_snapshot_hash": "sha256:" + "b" * 64,
            "degraded": False,
            "candidates": [{
                "name": "analyze",
                "description": "Synthetic test candidate",
                "content_hash": "sha256:" + "c" * 64,
            }],
        }

    def trace_reserve(self, **kwargs: object) -> dict[str, str | None]:
        self.reserved.append(kwargs)
        return {"turn_trace_id": "trace-1", "turn_token": "never-persisted", "run_id": "run-1", "bind_state": "normal"}

    def commit_route(
        self, route_token: str, workspace_id: str, session_id_hash: str, decision: dict[str, object]
    ) -> dict[str, object]:
        self.committed.append((route_token, workspace_id, session_id_hash, decision))
        return {"schema_version": "skilltree/v1", "run_id": "run-1", "selected_skill_name": "analyze"}

    def commit_current_route(
        self, workspace_id: str, session_id: str, turn_id: str, decision: dict[str, object] | None
    ) -> dict[str, object]:
        self.committed.append(("auto", workspace_id, session_id, decision or {"selected_skill_name": "analyze"}))
        return {"schema_version": "skilltree/v1", "run_id": "run-1", "selected_skill_name": "analyze"}


def test_disabled_consent_returns_empty_output_without_a_probe(tmp_path: Path) -> None:
    database = FakeDatabase(trace_capture_enabled=False)

    code, output = handle_hook_event(
        "UserPromptSubmit", _fixture("user-prompt-submit.json"),
        data_dir=tmp_path, plugin_root=ROOT / "plugins" / "skilltree", database=database,
    )

    assert (code, output) == (0, "")
    assert not (tmp_path / "diagnostics").exists()
    assert database.prepared == []


def test_user_prompt_emits_only_one_route_envelope_and_reserves_a_turn(tmp_path: Path) -> None:
    database = FakeDatabase()
    with patch("skilltree.hooks.hook_bridge.diagnose", return_value=(_ready_doctor(), 0)):
        code, output = handle_hook_event(
            "UserPromptSubmit", _fixture("user-prompt-submit.json"),
            data_dir=tmp_path, plugin_root=ROOT / "plugins" / "skilltree", database=database,
        )

    wrapper = json.loads(output)
    assert code == 0
    assert set(wrapper) == {"hookSpecificOutput"}
    assert wrapper["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert set(wrapper["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}
    envelope = json.loads(wrapper["hookSpecificOutput"]["additionalContext"])
    assert envelope["schema_version"] == "skilltree-route-envelope/v1"
    assert len(database.prepared) == 1
    assert len(database.reserved) == 1
    assert database.reserved[0]["route_token"] == envelope["route_token"]
    assert "synthetic prompt" not in output
    assert "workspace-ref" not in output


def test_invalid_doctor_failed_or_empty_route_user_prompt_fails_open(tmp_path: Path) -> None:
    database = FakeDatabase()
    with patch("skilltree.hooks.hook_bridge.diagnose", return_value=(_ready_doctor(), 0)):
        malformed = handle_hook_event(
            "UserPromptSubmit", b"{}", data_dir=tmp_path,
            plugin_root=ROOT / "plugins" / "skilltree", database=database,
        )
    with patch("skilltree.hooks.hook_bridge.diagnose", return_value=({"diagnostic_state": "failed"}, 2)):
        failed_doctor = handle_hook_event(
            "UserPromptSubmit", _fixture("user-prompt-submit.json"), data_dir=tmp_path,
            plugin_root=ROOT / "plugins" / "skilltree", database=database,
        )

    class EmptyRouteDatabase(FakeDatabase):
        def prepare_route(self, workspace_id: str, session_id_hash: str, prompt: str) -> dict[str, object]:
            self.prepared.append((workspace_id, session_id_hash, prompt))
            return {}

    empty_route = EmptyRouteDatabase()
    with patch("skilltree.hooks.hook_bridge.diagnose", return_value=(_ready_doctor(), 0)):
        missing_candidates = handle_hook_event(
            "UserPromptSubmit", _fixture("user-prompt-submit.json"), data_dir=tmp_path,
            plugin_root=ROOT / "plugins" / "skilltree", database=empty_route,
        )

    assert malformed == failed_doctor == missing_candidates == (0, "")
    assert database.prepared == []
    assert database.reserved == []
    assert empty_route.reserved == []


def test_stop_commits_exactly_one_final_route_decision_and_rejects_replay(tmp_path: Path) -> None:
    database = FakeDatabase()
    with patch("skilltree.hooks.hook_bridge.diagnose", return_value=(_ready_doctor(), 0)):
        first = handle_hook_event(
            "Stop", _fixture("stop.json"),
            data_dir=tmp_path, plugin_root=ROOT / "plugins" / "skilltree", database=database,
        )
        repeated = handle_hook_event(
            "Stop", _fixture("stop.json"),
            data_dir=tmp_path, plugin_root=ROOT / "plugins" / "skilltree", database=database,
        )

    assert first == (0, "")
    assert repeated == (0, "")
    assert len(database.committed) == 1


def test_stop_writes_shape_only_probe_without_message_values(tmp_path: Path) -> None:
    database = FakeDatabase()
    with patch("skilltree.hooks.hook_bridge.diagnose", return_value=(_ready_doctor(), 0)):
        result = handle_hook_event(
            "Stop", _fixture("stop.json"),
            data_dir=tmp_path, plugin_root=ROOT / "plugins" / "skilltree", database=database,
        )

    assert result == (0, "")
    probes = list((tmp_path / "diagnostics" / "g0.25").glob("*.json"))
    assert len(probes) == 1
    payload = json.loads(probes[0].read_text(encoding="utf-8"))
    assert payload["event_name"] == "Stop"
    assert payload["code"] == "observed"
    assert set(payload["field_shapes"]) == {"session_id", "cwd", "last_assistant_message"}
    serialized = probes[0].read_text(encoding="utf-8")
    assert "Synthetic response" not in serialized
    assert "session-ref" not in serialized
    assert "workspace-ref" not in serialized
    assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in serialized


def test_stop_does_not_commit_when_the_host_drops_the_decision_message(tmp_path: Path) -> None:
    database = FakeDatabase()
    raw = json.dumps({
        "session_id": "session-ref",
        "cwd": "workspace-ref",
        "last_assistant_message": "ordinary response",
    }).encode("utf-8")
    with patch("skilltree.hooks.hook_bridge.diagnose", return_value=(_ready_doctor(), 0)):
        result = handle_hook_event(
            "Stop", raw,
            data_dir=tmp_path, plugin_root=ROOT / "plugins" / "skilltree", database=database,
        )

    assert result == (0, "")
    probe = next((tmp_path / "diagnostics" / "g0.25").glob("*.json"))
    payload = json.loads(probe.read_text(encoding="utf-8"))
    assert payload["code"] == "decision_missing"
    assert database.committed == []


def test_malformed_stop_input_records_invalid_probe_and_fails_open(tmp_path: Path) -> None:
    database = FakeDatabase()
    with patch("skilltree.hooks.hook_bridge.diagnose", return_value=(_ready_doctor(), 0)):
        result = handle_hook_event(
            "Stop", b"{not-json",
            data_dir=tmp_path, plugin_root=ROOT / "plugins" / "skilltree", database=database,
        )

    assert result == (0, "")
    probe = next((tmp_path / "diagnostics" / "g0.25").glob("*.json"))
    payload = json.loads(probe.read_text(encoding="utf-8"))
    assert payload["code"] == "invalid_input"
    assert database.committed == []


def test_stop_probe_records_commit_error_and_fails_open(tmp_path: Path) -> None:
    class FailingDatabase(FakeDatabase):
        def commit_route(
            self, route_token: str, workspace_id: str, session_id_hash: str, decision: dict[str, object]
        ) -> dict[str, object]:
            raise RuntimeError("synthetic commit failure")

    database = FailingDatabase()
    with patch("skilltree.hooks.hook_bridge.diagnose", return_value=(_ready_doctor(), 0)):
        result = handle_hook_event(
            "Stop", _fixture("stop.json").replace(b"a" * 43, b"b" * 43),
            data_dir=tmp_path, plugin_root=ROOT / "plugins" / "skilltree", database=database,
        )

    assert result == (0, "")
    probe = next((tmp_path / "diagnostics" / "g0.25").glob("*.json"))
    payload = json.loads(probe.read_text(encoding="utf-8"))
    assert payload["code"] == "commit_error"


def test_pre_post_probes_are_shape_only_atomic_and_bounded(tmp_path: Path) -> None:
    event = parse_hook_input("PreToolUse", _fixture("pre-tool-use.json"))
    now = datetime(2026, 8, 15, tzinfo=UTC)
    probe = build_probe(event, "sha256:" + "d" * 64, now)
    serialized = json.dumps(probe, sort_keys=True)

    assert "synthetic-tool-payload" not in serialized
    assert "workspace-ref" not in serialized
    assert probe["event_name"] == "PreToolUse"
    assert probe["field_shapes"]["tool_input"]["json_type"] == "object"

    def write_one(index: int) -> None:
        write_probe(tmp_path, {**probe, "observation_id": f"observation-{index:03d}"}, now)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write_one, range(105)))

    files = list((tmp_path / "diagnostics" / "g0.25").glob("*.json"))
    assert len(files) == 100
    assert all(json.loads(path.read_text(encoding="utf-8"))["schema_version"] == "skilltree-hook-probe/v1" for path in files)

    old = files[0]
    old_time = (now - timedelta(hours=25)).timestamp()
    old.touch()
    import os
    os.utime(old, (old_time, old_time))
    write_probe(tmp_path, {**probe, "observation_id": "fresh-observation"}, now)
    assert not old.exists()


def test_pre_tool_hook_writes_a_probe_but_never_emits_stdout(tmp_path: Path) -> None:
    database = FakeDatabase()
    with patch("skilltree.hooks.hook_bridge.diagnose", return_value=(_ready_doctor(), 0)):
        result = handle_hook_event(
            "PreToolUse", _fixture("pre-tool-use.json"), data_dir=tmp_path,
            plugin_root=ROOT / "plugins" / "skilltree", database=database,
        )

    records = list((tmp_path / "diagnostics" / "g0.25").glob("*.json"))
    assert result == (0, "")
    assert len(records) == 1
    assert "synthetic-tool-payload" not in records[0].read_text(encoding="utf-8")


def test_runtime_handler_forwards_only_bridge_stdout(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("skilltree_runtime_hook", HANDLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = (0, '{"hookSpecificOutput":{"additionalContext":"safe"}}')

    monkeypatch.setattr(module, "handle_hook_event", lambda *args, **kwargs: expected)
    assert module.main(["UserPromptSubmit"], stdin_bytes=b"{}", environ={"PLUGIN_DATA": "data", "PLUGIN_ROOT": "root"}) == 0


def test_runtime_subprocess_missing_runtime_is_fail_open(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(HANDLER), "UserPromptSubmit"],
        input=b"not-json",
        capture_output=True,
        env={**os.environ, "PLUGIN_DATA": str(tmp_path / "data"), "PLUGIN_ROOT": str(ROOT / "plugins" / "skilltree")},
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == b""


def test_windows_invoke_hook_forwards_stdin_to_runtime_handler(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_data = tmp_path / "data"
    runtime = plugin_root / "runtime"
    scripts = plugin_root / "scripts"
    python_path = plugin_data / "venv" / "Scripts" / "python.exe"
    runtime.mkdir(parents=True)
    scripts.mkdir(parents=True)
    python_path.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, python_path)
    shutil.copy2(ROOT / ".venv" / "pyvenv.cfg", plugin_data / "venv" / "pyvenv.cfg")
    (runtime / "skilltree_hook.py").write_text(
        "import sys\nsys.stdout.write(sys.stdin.read())\n",
        encoding="utf-8",
    )
    invoke_hook = ROOT / "plugins" / "skilltree" / "scripts" / "invoke-hook.ps1"
    payload = b'{"hook_event_name":"UserPromptSubmit","prompt":"probe"}'

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(invoke_hook),
            "UserPromptSubmit",
        ],
        input=payload,
        capture_output=True,
        env={
            **os.environ,
            "PLUGIN_DATA": str(plugin_data),
            "PLUGIN_ROOT": str(plugin_root),
        },
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == json.loads(payload)


def _fixture(name: str) -> bytes:
    return (ROOT / "tests" / "fixtures" / "g025" / name).read_bytes()


def _ready_doctor() -> dict[str, object]:
    return {"diagnostic_state": "ready", "current_hook_bundle_hash": "sha256:" + "d" * 64}
