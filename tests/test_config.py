from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skilltree.config import RuntimeConfig, SkillRootError


class RuntimeConfigTests(unittest.TestCase):
    def test_defaults_are_privacy_preserving(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = RuntimeConfig.load(Path(temp_dir))

        self.assertIsNone(config.skill_root)
        self.assertFalse(config.trace_capture_enabled)
        self.assertFalse(config.memory_read_enabled)
        self.assertFalse(config.memory_write_enabled)
        self.assertFalse(config.replay_capture_enabled)

    def test_skill_root_requires_explicit_confirmation_and_absolute_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            skill_root = Path(temp_dir) / "skills"
            skill_root.mkdir()
            config = RuntimeConfig.load(data_dir)

            with self.assertRaises(SkillRootError):
                config.set_skill_root(skill_root, confirmed=False)
            with self.assertRaises(SkillRootError):
                config.set_skill_root(Path("relative"), confirmed=True)
            with self.assertRaises(SkillRootError):
                config.set_skill_root(Path("\\\\server\\share"), confirmed=True)

            config.set_skill_root(skill_root, confirmed=True)
            reloaded = RuntimeConfig.load(data_dir)

        self.assertEqual(reloaded.skill_root, skill_root.resolve())
