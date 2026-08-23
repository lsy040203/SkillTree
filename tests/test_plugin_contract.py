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

    def test_router_skill_consumes_only_hook_context_and_emits_the_p2_decision_receipt(self) -> None:
        skill = (PLUGIN_ROOT / "skills" / "skill-router" / "SKILL.md").read_text(encoding="utf-8")

        self.assertNotIn("skilltree doctor", skill)
        self.assertNotIn("$PLUGIN_DATA", skill)
        self.assertIn("skilltree-route-envelope/v1", skill)
        self.assertIn("skilltree-route-decision:", skill)
        self.assertIn("Never execute a Skill automatically", skill)

    def test_default_hooks_cover_only_declared_local_events(self) -> None:
        hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))

        self.assertEqual(set(hooks["hooks"]), {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"})
        for event_name, definitions in hooks["hooks"].items():
            expected_count = 2 if event_name == "UserPromptSubmit" else 1
            self.assertEqual(len(definitions), expected_count, event_name)
            for definition in definitions:
                handler = definition["hooks"][0]
                self.assertIn("commandWindows", handler)
                self.assertIn("timeout", handler)

        bootstrap = hooks["hooks"]["UserPromptSubmit"][1]["hooks"][0]
        self.assertIn("skilltree_bootstrap.ps1", bootstrap["commandWindows"])
        self.assertEqual(bootstrap["timeout"], 300)

        route_hook = hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        self.assertGreaterEqual(route_hook["timeout"], 5)
