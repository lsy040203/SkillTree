from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skilltree.outbox import AtomicOutbox, WriterLease
from skilltree.storage import Database


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


class StorageTests(unittest.TestCase):
    def test_database_initializes_versioned_schema_with_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "skilltree.sqlite3")
            database.initialize(PLUGIN_ROOT, target_schema_version=7)
            settings = database.read_runtime_settings()
            migrations = database.applied_migrations()

        self.assertEqual(migrations, list(range(1, 8)))
        self.assertEqual(
            settings,
            {
                "trace_capture_enabled": False,
                "memory_read_enabled": False,
                "memory_write_enabled": False,
                "replay_capture_enabled": False,
            },
        )

    def test_outbox_is_atomic_and_writer_lease_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            outbox = AtomicOutbox(data_dir / "outbox")
            ready_path = outbox.enqueue({"event_id": "event-1", "payload_hash": "sha256:test"})

            self.assertEqual(ready_path.parent.name, "ready")
            self.assertEqual(json.loads(ready_path.read_text(encoding="utf-8"))["event_id"], "event-1")
            self.assertFalse(list((data_dir / "outbox" / "staging").glob("*")))

            first = WriterLease(data_dir / "writer.lock", owner_id="first", ttl_seconds=60)
            second = WriterLease(data_dir / "writer.lock", owner_id="second", ttl_seconds=60)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
