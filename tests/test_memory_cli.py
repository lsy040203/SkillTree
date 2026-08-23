from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from skilltree.bundle import build_bundle
from skilltree.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


def test_memory_import_cli_returns_a_pending_candidate(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    build_bundle(ROOT)
    database = Database(data_dir / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=7)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("UPDATE runtime_config SET memory_write_enabled=1")
        connection.commit()
    source = tmp_path / "experience.md"
    source.write_text(
        "---\nkind: procedure\nscenario_key: p5\napplies_to: memory_design\n"
        "---\nReview candidates before approval.\n",
        encoding="utf-8",
    )

    result = _run_cli(
        "memory", "import", "--data-dir", str(data_dir), "--input", str(source),
        "--user-id", "user-1", "--workspace-id", "workspace-1",
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert payload["data"]["pending"] == 1


def test_memory_extract_cli_reports_missing_provider_configuration(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    build_bundle(ROOT)
    database = Database(data_dir / "skilltree.sqlite3")
    database.initialize(PLUGIN_ROOT, target_schema_version=7)

    environment = {key: value for key, value in os.environ.items() if not key.startswith("SKILLTREE_MEMORY_")}
    result = subprocess.run(
        [sys.executable, "-m", "skilltree", "memory", "extract",
         "--data-dir", str(data_dir), "--run-id", "run-1"],
        text=True,
        capture_output=True,
        env={**environment, "PYTHONPATH": str(ROOT / "src")},
        check=False,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["error"]["code"] == "configuration_invalid"


def test_profile_extract_request_requires_bounded_durable_statements(tmp_path: Path) -> None:
    from skilltree.interfaces.memory_io import MemoryRequestError, load_memory_request

    request = tmp_path / "profile.json"
    request.write_text(json.dumps({
        "schema_version": "skilltree/v1",
        "user_id": "local",
        "workspace_id": "workspace-1",
        "durable_preference_statements": ["Always explain in Chinese."],
    }), encoding="utf-8")

    loaded = load_memory_request(request, "profile-extract")

    assert loaded["durable_preference_statements"] == ["Always explain in Chinese."]

    request.write_text(json.dumps({
        "schema_version": "skilltree/v1",
        "user_id": "local",
        "workspace_id": "workspace-1",
        "durable_preference_statements": [""],
    }), encoding="utf-8")
    try:
        load_memory_request(request, "profile-extract")
    except MemoryRequestError as error:
        assert error.code == "invalid_schema"
    else:
        raise AssertionError("empty durable statement must be rejected")


def test_memory_write_degraded_error_is_retryable() -> None:
    import json as json_module

    from skilltree.application.cli import _emit_registry_error

    # The CLI envelope must distinguish a temporary breaker from permanent input errors.
    import contextlib
    import io

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert _emit_registry_error("memory_write_degraded") == 2
    assert json_module.loads(output.getvalue())["error"]["retryable"] is True


@pytest.mark.parametrize(
    ("command", "payload"),
    [
        ("list", {"schema_version": "skilltree/v1", "user_id": "local", "layer": "L1"}),
        ("delete", {"schema_version": "skilltree/v1", "user_id": "local", "layer": "L1", "handle": "preference.language"}),
    ],
)
def test_memory_l1_requests_do_not_require_workspace(tmp_path: Path, command: str, payload: dict[str, object]) -> None:
    from skilltree.interfaces.memory_io import load_memory_request

    request = tmp_path / f"{command}.json"
    request.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_memory_request(request, command)

    assert loaded["layer"] == "L1"


def test_memory_export_requires_workspace_and_rejects_unknown_fields(tmp_path: Path) -> None:
    from skilltree.interfaces.memory_io import MemoryRequestError, load_memory_request

    request = tmp_path / "export.json"
    request.write_text(json.dumps({
        "schema_version": "skilltree/v1",
        "user_id": "local",
        "workspace_id": "workspace-1",
    }), encoding="utf-8")
    assert load_memory_request(request, "export")["workspace_id"] == "workspace-1"

    request.write_text(json.dumps({
        "schema_version": "skilltree/v1",
        "user_id": "local",
        "workspace_id": "workspace-1",
        "include_pending": False,
    }), encoding="utf-8")
    with pytest.raises(MemoryRequestError, match="invalid_schema"):
        load_memory_request(request, "export")


def test_memory_delete_requires_exact_handle_and_rejects_id_alias(tmp_path: Path) -> None:
    from skilltree.interfaces.memory_io import MemoryRequestError, load_memory_request

    request = tmp_path / "delete.json"
    request.write_text(json.dumps({
        "schema_version": "skilltree/v1",
        "user_id": "local",
        "layer": "L1",
        "handle": "preference.language",
    }), encoding="utf-8")
    assert load_memory_request(request, "delete")["handle"] == "preference.language"

    request.write_text(json.dumps({
        "schema_version": "skilltree/v1",
        "user_id": "local",
        "layer": "L1",
        "id": "profile-1",
    }), encoding="utf-8")
    with pytest.raises(MemoryRequestError, match="invalid_schema"):
        load_memory_request(request, "delete")


def test_clear_requests_require_exact_confirmation(tmp_path: Path) -> None:
    from skilltree.interfaces.memory_io import MemoryRequestError, load_memory_request

    request = tmp_path / "clear.json"
    request.write_text(json.dumps({
        "schema_version": "skilltree/v1",
        "user_id": "local",
        "confirm": "DELETE_PROFILE",
    }), encoding="utf-8")
    assert load_memory_request(request, "clear-profile")["confirm"] == "DELETE_PROFILE"

    request.write_text(json.dumps({
        "schema_version": "skilltree/v1",
        "user_id": "local",
        "confirm": "delete_profile",
    }), encoding="utf-8")
    with pytest.raises(MemoryRequestError, match="authorization_required"):
        load_memory_request(request, "clear-profile")


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "skilltree", *arguments],
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
    )
