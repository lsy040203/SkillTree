from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"
BOOTSTRAP = PLUGIN_ROOT / "runtime" / "skilltree_bootstrap.ps1"
BOOTSTRAP_PYTHON = Path(
    os.environ.get(
        "SKILLTREE_UV_PYTHON",
        r"C:\Users\Lenovo\AppData\Roaming\uv\python\cpython-3.14.6-windows-x86_64-none\python.exe",
    )
)


pytestmark = pytest.mark.skipif(
    not BOOTSTRAP_PYTHON.is_file(), reason="requires the configured bootstrap Python"
)


def _run_setup(data_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PLUGIN_ROOT / "scripts" / "setup.ps1"),
            "-PluginData",
            str(data_dir),
            "-PythonPath",
            str(BOOTSTRAP_PYTHON),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _run_bootstrap(
    prompt: str,
    data_dir: Path,
    *,
    include_environment: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    if include_environment:
        environment.update({"PLUGIN_ROOT": str(PLUGIN_ROOT), "PLUGIN_DATA": str(data_dir)})
    else:
        environment.pop("PLUGIN_ROOT", None)
        environment.pop("PLUGIN_DATA", None)
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BOOTSTRAP),
        ],
        input=json.dumps({"prompt": prompt}),
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


def _reason(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == {"decision", "reason"}
    assert payload["decision"] == "block"
    return payload["reason"]


def _write_status_request(directory: Path) -> Path:
    path = directory / "status.json"
    path.write_text(
        json.dumps({"schema_version": "skilltree/v1", "user_id": "local"}),
        encoding="utf-8",
    )
    return path


def _write_consent_request(directory: Path, version: int, *, confirm: str = "SET_RUNTIME_CONSENT") -> Path:
    path = directory / "consent.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "skilltree/v1",
                "user_id": "local",
                "expected_config_version": version,
                "consents": {
                    "trace_capture_enabled": True,
                    "memory_read_enabled": False,
                    "memory_write_enabled": False,
                    "replay_capture_enabled": False,
                },
                "confirm": confirm,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_config_status_returns_bounded_runtime_state(tmp_path: Path) -> None:
    data_dir = tmp_path / "plugin-data"
    assert _run_setup(data_dir).returncode == 0
    request = _write_status_request(tmp_path)

    result = _run_bootstrap(f'$skilltree-bootstrap config status --input "{request}"', data_dir)

    assert _reason(result) == (
        "skilltree_config_status/v1;config_version=1;trace_capture_enabled=false;"
        "memory_read_enabled=false;memory_write_enabled=false;replay_capture_enabled=false"
    )


def test_config_set_consent_enables_only_trace_capture(tmp_path: Path) -> None:
    data_dir = tmp_path / "plugin-data"
    assert _run_setup(data_dir).returncode == 0
    request = _write_consent_request(tmp_path, 1)

    result = _run_bootstrap(f'$skilltree-bootstrap config set-consent --input "{request}"', data_dir)

    assert _reason(result) == "skilltree_config_updated/v1;config_version=2;changed_keys=trace_capture_enabled"
    with sqlite3.connect(data_dir / "skilltree.sqlite3") as connection:
        row = connection.execute(
            "SELECT config_version, trace_capture_enabled, memory_read_enabled, "
            "memory_write_enabled, replay_capture_enabled FROM runtime_config WHERE config_id = 1"
        ).fetchone()
    assert row == (2, 1, 0, 0, 0)


def test_config_set_consent_is_idempotent_at_the_current_version(tmp_path: Path) -> None:
    data_dir = tmp_path / "plugin-data"
    assert _run_setup(data_dir).returncode == 0
    first_request = _write_consent_request(tmp_path, 1)
    assert _reason(_run_bootstrap(f'$skilltree-bootstrap config set-consent --input "{first_request}"', data_dir)) == (
        "skilltree_config_updated/v1;config_version=2;changed_keys=trace_capture_enabled"
    )
    repeated_request = _write_consent_request(tmp_path, 2)

    repeated = _run_bootstrap(f'$skilltree-bootstrap config set-consent --input "{repeated_request}"', data_dir)

    assert _reason(repeated) == "skilltree_config_updated/v1;config_version=2;changed_keys=empty"


def test_config_set_consent_rejects_a_stale_version_without_writing(tmp_path: Path) -> None:
    data_dir = tmp_path / "plugin-data"
    assert _run_setup(data_dir).returncode == 0
    first_request = _write_consent_request(tmp_path, 1)
    _run_bootstrap(f'$skilltree-bootstrap config set-consent --input "{first_request}"', data_dir)
    stale_request = _write_consent_request(tmp_path, 1)

    result = _run_bootstrap(f'$skilltree-bootstrap config set-consent --input "{stale_request}"', data_dir)

    assert _reason(result) == "skilltree_config_failed:conflict"
    with sqlite3.connect(data_dir / "skilltree.sqlite3") as connection:
        assert connection.execute(
            "SELECT config_version, trace_capture_enabled FROM runtime_config WHERE config_id = 1"
        ).fetchone() == (2, 1)


def test_config_set_consent_rejects_invalid_schema_without_echoing_input(tmp_path: Path) -> None:
    data_dir = tmp_path / "plugin-data"
    assert _run_setup(data_dir).returncode == 0
    request = tmp_path / "sensitive-request.json"
    request.write_text(
        json.dumps({"schema_version": "skilltree/v1", "user_id": "local", "secret": "do-not-echo"}),
        encoding="utf-8",
    )

    result = _run_bootstrap(f'$skilltree-bootstrap config set-consent --input "{request}"', data_dir)

    assert _reason(result) == "skilltree_config_failed:invalid_schema"
    assert "sensitive-request.json" not in result.stdout
    assert "do-not-echo" not in result.stdout


def test_config_set_consent_rejects_wrong_confirmation_without_writing(tmp_path: Path) -> None:
    data_dir = tmp_path / "plugin-data"
    assert _run_setup(data_dir).returncode == 0
    request = _write_consent_request(tmp_path, 1, confirm="WRONG_CONFIRMATION")

    result = _run_bootstrap(f'$skilltree-bootstrap config set-consent --input "{request}"', data_dir)

    assert _reason(result) == "skilltree_config_failed:invalid_schema"
    with sqlite3.connect(data_dir / "skilltree.sqlite3") as connection:
        assert connection.execute(
            "SELECT config_version, trace_capture_enabled FROM runtime_config WHERE config_id = 1"
        ).fetchone() == (1, 0)


def test_config_messages_reject_near_matches_and_never_echo_request_paths(tmp_path: Path) -> None:
    data_dir = tmp_path / "plugin-data"
    assert _run_setup(data_dir).returncode == 0
    request = _write_status_request(tmp_path)

    for prompt in (
        "$skilltree-bootstrap config status",
        '$skilltree-bootstrap config status --input "relative.json"',
        f'$skilltree-bootstrap config status --input "{request}" --extra true',
        f'$skilltree-bootstrap config unknown --input "{request}"',
    ):
        result = _run_bootstrap(prompt, data_dir)
        reason = _reason(result)
        assert reason == "skilltree_config_failed:invalid_schema"
        assert str(request) not in result.stdout
        assert reason.isascii()
        assert len(reason.encode("ascii")) <= 256


def test_config_reports_a_bounded_internal_error_when_the_runtime_is_missing(tmp_path: Path) -> None:
    data_dir = tmp_path / "plugin-data"
    assert _run_setup(data_dir).returncode == 0
    shutil.rmtree(data_dir / "venv")
    request = _write_status_request(tmp_path)

    result = _run_bootstrap(f'$skilltree-bootstrap config status --input "{request}"', data_dir)

    assert _reason(result) == "skilltree_config_failed:internal_error"


def test_non_control_prompt_and_missing_environment_fail_open(tmp_path: Path) -> None:
    ordinary = _run_bootstrap("an ordinary prompt", tmp_path / "ordinary")
    missing_environment = _run_bootstrap(
        '$skilltree-bootstrap config status --input "C:\\missing.json"',
        tmp_path / "missing",
        include_environment=False,
    )

    assert ordinary.returncode == 0
    assert ordinary.stdout == ""
    assert missing_environment.returncode == 0
    assert missing_environment.stdout == ""
