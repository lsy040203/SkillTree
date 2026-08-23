from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from skilltree.bundle import build_bundle
from skilltree.core import routing_catalog
from skilltree.storage import Database, RegistryStorageError, ROUTE_TOP_K


ROOT = Path(__file__).resolve().parents[1]


def test_route_top_k_is_eight() -> None:
    assert ROUTE_TOP_K == 8


def test_route_prepare_and_fallback_return_trusted_and_pending_candidates() -> None:
    build_bundle(ROOT)
    with tempfile.TemporaryDirectory() as temp_dir:
        database = Database(Path(temp_dir) / "skilltree.sqlite3")
        database.initialize(ROOT / "plugins" / "skilltree", target_schema_version=7)
        with closing(sqlite3.connect(database.path)) as connection:
            rows = [
                ("analyze", "Analyze repositories", "C:/safe/analyze/SKILL.md", _hash("analyze"), "trusted"),
                ("code-review", "Review code changes", "C:/safe/code-review/SKILL.md", _hash("code-review"), "trusted"),
                ("lsp", "Navigate source symbols", "C:/safe/lsp/SKILL.md", _hash("lsp"), "trusted"),
                ("pending-skill", "Pending skill", "C:/safe/pending/SKILL.md", _hash("pending"), "pending"),
                ("blocked-skill", "Blocked skill", "C:/safe/blocked/SKILL.md", _hash("blocked"), "blocked"),
            ]
            connection.executemany(
                "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) VALUES (?, ?, ?, ?, ?, NULL, ?)",
                [(*row, "2026-08-14T00:00:00Z") for row in rows],
            )
            connection.execute("UPDATE runtime_config SET trace_capture_enabled = 1 WHERE config_id = 1")
            connection.commit()

        prompt = "analyze review source"
        envelope = database.prepare_route("sha256:" + "a" * 64, "sha256:" + "b" * 64, prompt)
        fallback = database.list_route_candidates(prompt)

    assert [item["name"] for item in envelope["candidates"]] == ["analyze", "code-review", "lsp", "pending-skill"]
    assert [item["name"] for item in fallback["candidates"]] == ["analyze", "code-review", "lsp", "pending-skill"]
    assert "blocked-skill" not in str(envelope)


def test_route_prepare_and_fallback_include_pending_and_invalid_without_leaking_metadata() -> None:
    build_bundle(ROOT)
    with tempfile.TemporaryDirectory() as temp_dir:
        database = Database(Path(temp_dir) / "skilltree.sqlite3")
        database.initialize(ROOT / "plugins" / "skilltree", target_schema_version=7)
        with closing(sqlite3.connect(database.path)) as connection:
            rows = [
                ("trusted", "Trusted skill", "C:/safe/trusted/SKILL.md", _hash("trusted"), "trusted"),
                ("pending", "Pending skill", "C:/safe/pending/SKILL.md", _hash("pending"), "pending"),
                ("invalid-abcdef123456", "Unvalidated Skill metadata", "C:/private/invalid/SKILL.md", _hash("invalid"), "invalid"),
                ("blocked", "Blocked skill", "C:/safe/blocked/SKILL.md", _hash("blocked"), "blocked"),
            ]
            connection.executemany(
                "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) VALUES (?, ?, ?, ?, ?, NULL, ?)",
                [(*row, "2026-08-14T00:00:00Z") for row in rows],
            )
            connection.execute("UPDATE runtime_config SET trace_capture_enabled = 1 WHERE config_id = 1")
            connection.commit()

        envelope = database.prepare_route("sha256:" + "a" * 64, "sha256:" + "b" * 64, "skill")
        fallback = database.list_route_candidates("skill")

    assert [item["name"] for item in envelope["candidates"]] == ["invalid-abcdef123456", "pending", "trusted"]
    assert [item["name"] for item in fallback["candidates"]] == ["invalid-abcdef123456", "pending", "trusted"]
    assert envelope["candidates"] == fallback["candidates"]
    assert all(set(item) == {"name", "description", "content_hash"} for item in envelope["candidates"])
    assert "C:/private" not in str(envelope)
    assert "diagnostic" not in str(envelope)


def test_route_catalog_exposes_ten_visible_candidates_and_fallback_marks_degraded() -> None:
    build_bundle(ROOT)
    with tempfile.TemporaryDirectory() as temp_dir:
        database = Database(Path(temp_dir) / "skilltree.sqlite3")
        database.initialize(ROOT / "plugins" / "skilltree", target_schema_version=7)
        with closing(sqlite3.connect(database.path)) as connection:
            connection.executemany(
                "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) VALUES (?, ?, ?, ?, 'trusted', NULL, ?)",
                [
                    (f"skill-{index:02d}", "Generic trusted skill", f"C:/safe/skill-{index:02d}/SKILL.md", _hash(f"skill-{index:02d}"), "2026-08-14T00:00:00Z")
                    for index in range(10)
                ],
            )
            connection.execute("UPDATE runtime_config SET trace_capture_enabled = 1 WHERE config_id = 1")
            connection.commit()

        envelope = database.prepare_route("sha256:" + "a" * 64, "sha256:" + "b" * 64, "unrelated prompt")
        fallback = database.list_route_candidates("unrelated prompt")

    expected = [f"skill-{index:02d}" for index in range(10)]
    assert envelope["degraded"] is False
    assert [item["name"] for item in envelope["candidates"]] == expected
    assert fallback["degraded"] is True
    assert [item["name"] for item in fallback["candidates"]] == expected


def test_route_catalog_overflow_uses_lexical_top_k_and_marks_degraded(monkeypatch) -> None:
    build_bundle(ROOT)
    with tempfile.TemporaryDirectory() as temp_dir:
        database = Database(Path(temp_dir) / "skilltree.sqlite3")
        database.initialize(ROOT / "plugins" / "skilltree", target_schema_version=7)
        with closing(sqlite3.connect(database.path)) as connection:
            connection.executemany(
                "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) VALUES (?, ?, ?, ?, 'trusted', NULL, ?)",
                [
                    (f"skill-{index:02d}", "generic routing skill", f"C:/safe/skill-{index:02d}/SKILL.md", _hash(f"skill-{index:02d}"), "2026-08-14T00:00:00Z")
                    for index in range(10)
                ],
            )
            connection.commit()

        monkeypatch.setattr(routing_catalog, "ROUTE_CATALOG_MAX_BYTES", 64)
        envelope = database.prepare_route("sha256:" + "a" * 64, "sha256:" + "b" * 64, "skill-09")

    assert envelope["degraded"] is True
    assert len(envelope["candidates"]) == ROUTE_TOP_K
    assert envelope["candidates"][0]["name"] == "skill-09"


def test_route_prepare_uses_only_trusted_candidates_and_persists_no_prompt() -> None:
    build_bundle(ROOT)
    with tempfile.TemporaryDirectory() as temp_dir:
        database = Database(Path(temp_dir) / "skilltree.sqlite3")
        database.initialize(ROOT / "plugins" / "skilltree", target_schema_version=7)
        with closing(sqlite3.connect(database.path)) as connection:
            connection.executemany(
                "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) VALUES (?, ?, ?, ?, ?, NULL, ?)",
                [
                    ("analyze", "Analyze repositories", "C:/safe/analyze/SKILL.md", _hash("analyze"), "trusted", "2026-08-14T00:00:00Z"),
                    ("blocked", "Never expose", "C:/safe/blocked/SKILL.md", _hash("blocked"), "blocked", "2026-08-14T00:00:00Z"),
                    ("lsp", "Navigate source symbols", "C:/safe/lsp/SKILL.md", _hash("lsp"), "trusted", "2026-08-14T00:00:00Z"),
                ],
            )
            connection.commit()

        envelope = database.prepare_route("sha256:" + "a" * 64, "sha256:" + "b" * 64, "analyze this repository")
        with closing(sqlite3.connect(database.path)) as connection:
            offer = connection.execute("SELECT candidate_json FROM route_offers").fetchone()[0]

    assert envelope["schema_version"] == "skilltree-route-envelope/v1"
    assert [item["name"] for item in envelope["candidates"]] == ["analyze", "lsp"]
    assert "blocked" not in str(envelope)
    assert "repository" not in offer
    assert "C:/safe" not in str(envelope)
    assert len(envelope["route_token"]) >= 32


def test_route_candidates_returns_trusted_candidates_without_creating_offer() -> None:
    database, workspace, session, _ = _prepared_route()
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("UPDATE runtime_config SET trace_capture_enabled = 1 WHERE config_id = 1")
        connection.commit()

    result = database.list_route_candidates("analyze repository")

    assert result["schema_version"] == "skilltree-route-candidates/v1"
    assert [item["name"] for item in result["candidates"]] == ["analyze"]
    assert _route_table_counts(database)[0] == 1
    assert "path" not in str(result)


def test_route_commit_accepts_only_the_offer_candidates_and_consumes_its_token() -> None:
    build_bundle(ROOT)
    with tempfile.TemporaryDirectory() as temp_dir:
        database = Database(Path(temp_dir) / "skilltree.sqlite3")
        database.initialize(ROOT / "plugins" / "skilltree", target_schema_version=7)
        with closing(sqlite3.connect(database.path)) as connection:
            connection.execute(
                "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) VALUES (?, ?, ?, ?, 'trusted', NULL, ?)",
                ("analyze", "Analyze repositories", "C:/safe/analyze/SKILL.md", _hash("analyze"), "2026-08-14T00:00:00Z"),
            )
            connection.commit()
        workspace = "sha256:" + "a" * 64
        session = "sha256:" + "b" * 64
        envelope = database.prepare_route(workspace, session, "analyze repository")
        decision = {
            "schema_version": "skilltree/v1",
            "intent": {"name": "repository_analysis", "confidence": 0.9},
            "constraints": ["read_only"],
            "ranked_candidates": [{"name": "analyze", "rank": 1, "reason": "best match"}],
            "selected_skill_name": "analyze",
            "ordered_skill_names": ["analyze"],
            "degraded": False,
        }

        committed = database.commit_route(envelope["route_token"], workspace, session, decision)
        with self_assert_raises("conflict"):
            database.commit_route(envelope["route_token"], workspace, session, decision)
        with closing(sqlite3.connect(database.path)) as connection:
            offers = connection.execute("SELECT COUNT(*) FROM route_offers").fetchone()[0]
            decisions = connection.execute("SELECT COUNT(*) FROM route_decisions").fetchone()[0]

    assert committed["selected_skill_name"] == "analyze"
    assert offers == 0
    assert decisions == 1


def test_route_commit_normalizes_compact_summary_for_legacy_router_output() -> None:
    database, workspace, session, envelope = _prepared_route()
    compact = {
        "selected_skill_name": "analyze",
        "ordered_skill_names": ["analyze"],
    }

    committed = database.commit_route(envelope["route_token"], workspace, session, compact)

    assert committed["selected_skill_name"] == "analyze"
    with closing(sqlite3.connect(database.path)) as connection:
        stored = connection.execute("SELECT decision_json FROM route_decisions").fetchone()[0]
    assert '"degraded":true' in stored
    assert '"selected_skill_name":"analyze"' in stored


def test_route_commit_accepts_display_summary_fields_in_route_marker() -> None:
    database, workspace, session, envelope = _prepared_route()
    marker_summary = {
        "schema_version": "skilltree/v1",
        "selected_skill_name": "analyze",
        "ordered_skill_names": ["analyze"],
        "confidence": 0.94,
        "degraded": False,
    }

    committed = database.commit_route(envelope["route_token"], workspace, session, marker_summary)

    assert committed["selected_skill_name"] == "analyze"
    with closing(sqlite3.connect(database.path)) as connection:
        stored = connection.execute("SELECT decision_json FROM route_decisions").fetchone()[0]
    assert '"selected_skill_name":"analyze"' in stored
    assert '"degraded":false' in stored


def test_route_commit_derives_order_for_selected_only_summary() -> None:
    database, workspace, session, envelope = _prepared_route()

    database.commit_route(envelope["route_token"], workspace, session, {"selected_skill_name": "analyze"})

    with closing(sqlite3.connect(database.path)) as connection:
        stored = connection.execute("SELECT decision_json FROM route_decisions").fetchone()[0]
    assert '"ordered_skill_names":["analyze"]' in stored


def test_route_commit_rejects_a_decision_that_selects_a_skill_outside_the_offer_without_writing() -> None:
    database, workspace, session, envelope = _prepared_route()
    decision = _valid_decision()
    decision["selected_skill_name"] = "blocked"
    decision["ordered_skill_names"] = ["blocked"]

    with self_assert_raises("invalid_schema"):
        database.commit_route(envelope["route_token"], workspace, session, decision)

    assert _route_table_counts(database) == (1, 0, 0)


def test_route_commit_rejects_an_expired_offer_without_writing() -> None:
    database, workspace, session, envelope = _prepared_route()
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "UPDATE route_offers SET expires_at = ?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),),
        )
        connection.commit()

    with self_assert_raises("route_token_invalid"):
        database.commit_route(envelope["route_token"], workspace, session, _valid_decision())

    assert _route_table_counts(database) == (1, 0, 0)


def test_route_commit_allows_only_one_concurrent_consumer_of_a_route_token() -> None:
    database, workspace, session, envelope = _prepared_route()

    def commit() -> str:
        try:
            database.commit_route(envelope["route_token"], workspace, session, _valid_decision())
        except RegistryStorageError as error:
            return error.code
        return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: commit(), range(2)))

    assert results.count("success") == 1
    assert results.count("conflict") == 1
    assert _route_table_counts(database) == (0, 1, 1)


class self_assert_raises:
    def __init__(self, code: str) -> None:
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        return exception is not None and self.code in str(exception)


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _prepared_route() -> tuple[Database, str, str, dict[str, object]]:
    build_bundle(ROOT)
    temp_dir = tempfile.TemporaryDirectory()
    database = Database(Path(temp_dir.name) / "skilltree.sqlite3")
    database.initialize(ROOT / "plugins" / "skilltree", target_schema_version=7)
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute(
            "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) VALUES (?, ?, ?, ?, 'trusted', NULL, ?)",
            ("analyze", "Analyze repositories", "C:/safe/analyze/SKILL.md", _hash("analyze"), "2026-08-14T00:00:00Z"),
        )
        connection.commit()
    workspace = "sha256:" + "a" * 64
    session = "sha256:" + "b" * 64
    envelope = database.prepare_route(workspace, session, "analyze repository")
    # Keep the TemporaryDirectory alive for the Database lifetime in this test.
    database._test_temp_dir = temp_dir  # type: ignore[attr-defined]
    return database, workspace, session, envelope


def _valid_decision() -> dict[str, object]:
    return {
        "schema_version": "skilltree/v1",
        "intent": {"name": "repository_analysis", "confidence": 0.9},
        "constraints": ["read_only"],
        "ranked_candidates": [{"name": "analyze", "rank": 1, "reason": "best match"}],
        "selected_skill_name": "analyze",
        "ordered_skill_names": ["analyze"],
        "degraded": False,
    }


def _route_table_counts(database: Database) -> tuple[int, int, int]:
    with closing(sqlite3.connect(database.path)) as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("route_offers", "run_contexts", "route_decisions")
        )
