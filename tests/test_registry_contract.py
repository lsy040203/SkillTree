from __future__ import annotations

import json
import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT / "src"))

from skilltree.registry import RegistryError, discover_setup_candidates, scan_skill_root
from skilltree.registry_io import RegistryInputError, load_registry_request
from skilltree.storage import Database, RegistryStorageError


class RegistryInputContractTests(unittest.TestCase):
    def test_setup_request_accepts_only_its_exact_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "skills"
            root.mkdir()
            request = _write_request(
                Path(temp_dir),
                {
                    "schema_version": "skilltree/v1",
                    "user_id": "local",
                    "provided_root": str(root),
                    "selected_root": str(root),
                    "confirm": "SET_SKILL_ROOT",
                },
            )

            loaded = load_registry_request(request, "setup")

        self.assertEqual(loaded["selected_root"], str(root))

    def test_input_rejects_duplicate_keys_unknown_fields_and_null(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            duplicate = directory / "duplicate.json"
            duplicate.write_text(
                '{"schema_version":"skilltree/v1","schema_version":"skilltree/v1","user_id":"local"}',
                encoding="utf-8",
            )
            unknown = _write_request(
                directory,
                {"schema_version": "skilltree/v1", "user_id": "local", "extra": "value"},
            )
            null_value = _write_request(
                directory,
                {"schema_version": "skilltree/v1", "user_id": None},
                name="null.json",
            )

            for request in (duplicate, unknown, null_value):
                with self.subTest(request=request.name), self.assertRaisesRegex(RegistryInputError, "invalid_schema"):
                    load_registry_request(request, "scan")

    def test_input_requires_an_absolute_utf8_json_file_of_at_most_16_kib(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            oversized = directory / "oversized.json"
            oversized.write_bytes(b"{" + b" " * (16 * 1024) + b"}")

            with self.assertRaisesRegex(RegistryInputError, "invalid_schema"):
                load_registry_request(Path("relative.json"), "scan")
            with self.assertRaisesRegex(RegistryInputError, "invalid_schema"):
                load_registry_request(oversized, "scan")

    def test_request_rejects_wrong_shared_constants_and_command_specific_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            wrong_version = _write_request(directory, {"schema_version": "skilltree/v2", "user_id": "local"})
            wrong_user = _write_request(
                directory,
                {"schema_version": "skilltree/v1", "user_id": "other"},
                name="wrong-user.json",
            )
            relative_root = _write_request(
                directory,
                {
                    "schema_version": "skilltree/v1",
                    "user_id": "local",
                    "selected_root": "relative",
                    "confirm": "SET_SKILL_ROOT",
                },
                name="relative-root.json",
            )

            for request, command in ((wrong_version, "scan"), (wrong_user, "scan"), (relative_root, "setup")):
                with self.subTest(request=request.name), self.assertRaisesRegex(RegistryInputError, "invalid_schema"):
                    load_registry_request(request, command)


class RegistryDiscoveryContractTests(unittest.TestCase):
    def test_setup_candidate_discovery_uses_only_existing_configured_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            provided = directory / "provided"
            codex_home = directory / "codex-home"
            home = directory / "home"
            for root in (provided, codex_home / "skills", home / ".codex" / "skills"):
                root.mkdir(parents=True)

            candidates = discover_setup_candidates(
                str(provided),
                environ={"CODEX_HOME": str(codex_home)},
                home=home,
            )

        self.assertEqual(candidates, [provided.resolve(), (codex_home / "skills").resolve(), (home / ".codex" / "skills").resolve()])

    def test_scan_parses_only_supported_frontmatter_and_sanitizes_description(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "skills"
            _write_skill(root / "safe" / "SKILL.md", "safe-skill", "Contact test@example.com for setup.")
            _write_skill(root / "secret" / "SKILL.md", "secret-skill", "token=ghp_abcdefghijklmnopqrstuvwxyz1234567890")
            _write_skill(root / "invalid" / "SKILL.md", "Invalid_Name", "still invalid")

            records = scan_skill_root(root)
            records_by_name = {record.name: record for record in records}
            invalid_name_record = next(record for record in records if record.diagnostic == "invalid_name")

        self.assertEqual(records_by_name["safe-skill"].state, "trusted")
        self.assertEqual(records_by_name["safe-skill"].description, "Contact [REDACTED:email] for setup.")
        self.assertEqual(records_by_name["secret-skill"].state, "trusted")
        self.assertEqual(records_by_name["secret-skill"].description, "User-managed Skill: secret-skill")
        self.assertEqual(records_by_name["secret-skill"].diagnostic, "description_rejected")
        self.assertEqual(invalid_name_record.state, "trusted")

    def test_scan_checks_capacity_before_parsing_any_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "skills"
            for index in range(501):
                (root / f"skill-{index}" / "SKILL.md").parent.mkdir(parents=True)
                (root / f"skill-{index}" / "SKILL.md").write_text("not valid frontmatter", encoding="utf-8")

            with self.assertRaisesRegex(RegistryError, "registry_capacity_exceeded"):
                scan_skill_root(root)

    def test_scan_rewrites_overlong_description_and_trusts_user_managed_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "skills"
            long_description = "A useful user-managed skill. " + ("details " * 120)
            _write_skill(root / "long" / "SKILL.md", "long-skill", long_description)

            record = scan_skill_root(root)[0]

        self.assertEqual(record.state, "trusted")
        self.assertEqual(record.diagnostic, "description_truncated")
        self.assertLessEqual(len(record.description.encode("utf-8")), 500)
        self.assertTrue(record.description.startswith("A useful user-managed skill."))

    def test_apply_scan_replaces_legacy_invalid_name_for_the_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            root = directory / "skills"
            skill_path = root / "broken" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            skill_path.write_text("not frontmatter", encoding="utf-8")
            database = _initialized_database(directory)
            database.configure_skill_root(root, [root])
            with closing(sqlite3.connect(database.path)) as connection:
                connection.execute(
                    "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        "invalid-legacy-path-hash",
                        "Unvalidated Skill metadata",
                        str(skill_path.resolve()),
                        "sha256:" + "0" * 64,
                        "invalid",
                        "frontmatter_invalid",
                        "2026-08-14T00:00:00Z",
                    ),
                )
                connection.commit()

            result = database.apply_scan(scan_skill_root(root))
            status = database.registry_status()

        self.assertEqual(result["invalid_count"], 0)
        self.assertEqual(status["count"], 1)
        self.assertTrue(status["skills"][0]["name"].startswith("invalid-"))
        self.assertNotEqual(status["skills"][0]["name"], "invalid-legacy-path-hash")

    def test_apply_scan_migrates_legacy_empty_invalid_description_before_out_of_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            root = directory / "skills"
            legacy_path = root / "removed" / "SKILL.md"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text("old", encoding="utf-8")
            database = _initialized_database(directory)
            database.configure_skill_root(root, [root])
            with closing(sqlite3.connect(database.path)) as connection:
                connection.execute(
                    "INSERT INTO skills(name, description, path, content_hash, state, diagnostic, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        "invalid-old",
                        "",
                        str(legacy_path.resolve()),
                        "sha256:" + "0" * 64,
                        "invalid",
                        "frontmatter_invalid",
                        "2026-08-14T00:00:00Z",
                    ),
                )
                connection.commit()
            legacy_path.unlink()

            database.apply_scan([])
            status = database.registry_status()

        self.assertEqual(status["skills"][0]["state"], "out_of_scope")
        self.assertEqual(status["skills"][0]["description"], "Unvalidated Skill metadata")

    def test_scan_excludes_system_skills_and_tolerates_invalid_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "skills"
            (root / ".system" / "hidden").mkdir(parents=True)
            (root / ".system" / "hidden" / "SKILL.md").write_text(
                "---\nname: hidden\ndescription: hidden\n---\n", encoding="utf-8"
            )
            malformed = root / "malformed" / "SKILL.md"
            malformed.parent.mkdir(parents=True)
            malformed.write_text("not frontmatter", encoding="utf-8")
            missing_description = root / "missing-description" / "SKILL.md"
            missing_description.parent.mkdir(parents=True)
            missing_description.write_text("---\nname: missing-description\n---\n", encoding="utf-8")

            records = scan_skill_root(root)
            malformed_hash = "sha256:" + hashlib.sha256(malformed.read_bytes()).hexdigest()

        names = {record.name for record in records}
        malformed_record = next(record for record in records if record.content_hash == malformed_hash)
        missing_record = next(record for record in records if record.name == "missing-description")
        self.assertNotIn("hidden", names)
        self.assertEqual(malformed_record.name, "invalid-" + malformed_hash[7:19])
        self.assertEqual(malformed_record.state, "trusted")
        self.assertEqual(missing_record.description, "User-managed Skill: missing-description")
        self.assertEqual(missing_record.state, "trusted")


class RegistryStorageContractTests(unittest.TestCase):
    def test_setup_switches_old_entries_out_of_scope_and_audits_only_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            first_root = directory / "first"
            second_root = directory / "second"
            first_root.mkdir()
            second_root.mkdir()
            first_records = _scanned_records(first_root, "first-skill", "First skill")
            database = _initialized_database(directory)

            first_setup = database.configure_skill_root(first_root, [first_root])
            database.apply_scan(first_records)
            second_setup = database.configure_skill_root(second_root, [second_root])
            status = database.registry_status()
            audit_handles = _audit_handles(database.path)

        self.assertEqual(first_setup["config_version"], 2)
        self.assertEqual(second_setup["config_version"], 3)
        self.assertEqual(status["skills"][0]["state"], "out_of_scope")
        self.assertNotIn(str(first_root), "".join(audit_handles))
        self.assertNotIn(str(second_root), "".join(audit_handles))

    def test_setup_rejects_plugin_data_workspace_and_their_parent_or_child_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            skill_root = directory / "skills"
            plugin_data = directory / "plugin-data"
            skill_root.mkdir()
            plugin_data.mkdir()
            database = _initialized_database(directory)

            with self.assertRaisesRegex(RegistryStorageError, "out_of_scope"):
                database.configure_skill_root(skill_root, [skill_root], forbidden_roots=[directory])
            with self.assertRaisesRegex(RegistryStorageError, "out_of_scope"):
                database.configure_skill_root(plugin_data, [plugin_data], forbidden_roots=[plugin_data])

    def test_scan_hash_change_keeps_user_root_skill_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            root = directory / "skills"
            root.mkdir()
            database = _initialized_database(directory)
            database.configure_skill_root(root, [root])
            initial_records = _scanned_records(root, "sample-skill", "Initial description")
            database.apply_scan(initial_records)
            updated_records = _scanned_records(root, "sample-skill", "Updated description")

            result = database.apply_scan(updated_records)
            status = database.registry_status()

        self.assertEqual(result["invalid_count"], 0)
        self.assertEqual(status["skills"][0]["state"], "trusted")
        self.assertEqual(status["skills"][0]["content_hash"], updated_records[0].content_hash)

    def test_scan_path_change_keeps_user_root_skill_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            root = directory / "skills"
            root.mkdir()
            database = _initialized_database(directory)
            database.configure_skill_root(root, [root])
            initial_records = _scanned_records(root, "sample-skill", "Description")
            database.apply_scan(initial_records)
            original = root / "sample-skill" / "SKILL.md"
            moved = root / "moved-skill" / "SKILL.md"
            moved.parent.mkdir()
            original.rename(moved)

            database.apply_scan(scan_skill_root(root))
            status = database.registry_status()

        self.assertEqual(status["skills"][0]["state"], "trusted")

    def test_explicit_trust_is_idempotently_rejected_after_auto_trust(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            root = directory / "skills"
            root.mkdir()
            database = _initialized_database(directory)
            database.configure_skill_root(root, [root])
            records = _scanned_records(root, "sample-skill", "Description")
            database.apply_scan(records)
            content_hash = records[0].content_hash

            with self.assertRaisesRegex(RegistryStorageError, "not_found"):
                database.set_trust_state("missing-skill", content_hash, "trusted")
            with self.assertRaisesRegex(RegistryStorageError, "conflict"):
                database.set_trust_state("sample-skill", "sha256:" + "0" * 64, "trusted")
            with self.assertRaisesRegex(RegistryStorageError, "conflict"):
                database.set_trust_state("sample-skill", content_hash, "trusted")
            current_state = database.registry_status()["skills"][0]["state"]

        self.assertEqual(current_state, "trusted")

    def test_status_is_name_sorted_and_does_not_expose_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            root = directory / "skills"
            root.mkdir()
            database = _initialized_database(directory)
            database.configure_skill_root(root, [root])
            _write_skill(root / "zulu-skill" / "SKILL.md", "zulu-skill", "Zulu description")
            _write_skill(root / "alpha-skill" / "SKILL.md", "alpha-skill", "Alpha description")
            records = scan_skill_root(root)
            database.apply_scan(records)

            status = database.registry_status()

        self.assertEqual([item["name"] for item in status["skills"]], ["alpha-skill", "zulu-skill"])
        self.assertNotIn("path", status["skills"][0])
        self.assertNotIn(str(root), json.dumps(status))

    def test_scan_requires_a_confirmed_root_and_capacity_failure_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            root = directory / "skills"
            root.mkdir()
            database = _initialized_database(directory)
            with self.assertRaisesRegex(RegistryStorageError, "authorization_required"):
                database.apply_scan([])

            database.configure_skill_root(root, [root])
            existing_records = _scanned_records(root, "existing-skill", "Existing")
            database.apply_scan(existing_records)
            overflowing_records = existing_records * 501
            with self.assertRaisesRegex(RegistryStorageError, "registry_capacity_exceeded"):
                database.apply_scan(overflowing_records)
            status = database.registry_status()

        self.assertEqual([item["name"] for item in status["skills"]], ["existing-skill"])


def _write_request(directory: Path, payload: dict[str, object], *, name: str = "request.json") -> Path:
    request = directory / name
    request.write_text(json.dumps(payload), encoding="utf-8")
    return request


def _write_skill(path: Path, name: str, description: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n# Skill\n", encoding="utf-8")


def _initialized_database(directory: Path) -> Database:
    from skilltree.bundle import build_bundle

    build_bundle(ROOT)
    database = Database(directory / "data" / "skilltree.sqlite3")
    database.initialize(ROOT / "plugins" / "skilltree", target_schema_version=7)
    return database


def _scanned_records(root: Path, name: str, description: str):
    _write_skill(root / name / "SKILL.md", name, description)
    return scan_skill_root(root)


def _audit_handles(database_path: Path) -> list[str]:
    import sqlite3

    with closing(sqlite3.connect(database_path)) as connection:
        return [row[0] for row in connection.execute("SELECT object_handle_hash FROM audit_events")]
