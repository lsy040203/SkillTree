from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "skilltree"


class PluginContractTests(unittest.TestCase):
    def test_manifest_is_minimal_and_skill_is_discoverable(self) -> None:
        manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "skilltree")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertNotIn("hooks", manifest)
        self.assertTrue((PLUGIN_ROOT / "skills" / "skill-router" / "SKILL.md").is_file())

    def test_default_hooks_cover_only_declared_local_events(self) -> None:
        hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))

        self.assertEqual(set(hooks["hooks"]), {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"})
        for event_name, definitions in hooks["hooks"].items():
            self.assertEqual(len(definitions), 1, event_name)
            handler = definitions[0]["hooks"][0]
            self.assertIn("commandWindows", handler)
            self.assertIn("timeout", handler)
