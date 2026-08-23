# SkillTree compatibility matrix

This matrix is deliberately conservative. A `supported` row means that the
repository contains the named evidence for that exact surface; it is not a
promise for every future Codex client or Tool implementation.

```skilltree-compatibility/v1
{
  "schema_version": "skilltree-compatibility/v1",
  "entries": [
    {
      "surface": "os",
      "value": "Windows PowerShell",
      "status": "supported",
      "evidence": "tests/test_release_validator.py"
    },
    {
      "surface": "python",
      "value": ">=3.11",
      "status": "supported",
      "evidence": "pyproject.toml"
    },
    {
      "surface": "codex",
      "value": "Hook fixture contract",
      "status": "supported",
      "evidence": "docs/verification/G0.25-hook-context-output.md"
    },
    {
      "surface": "hook",
      "value": "UserPromptSubmit,PreToolUse,PostToolUse,Stop",
      "status": "supported",
      "evidence": "tests/test_hook_bridge.py"
    },
    {
      "surface": "tool",
      "value": "registered local summaries",
      "status": "supported",
      "evidence": "DEV_SPEC.md"
    },
    {
      "surface": "replay",
      "value": "fixture-only paired baseline/candidate",
      "status": "supported",
      "evidence": "tests/test_replay_evaluation.py"
    },
    {
      "surface": "codex",
      "value": "arbitrary desktop versions",
      "status": "unsupported",
      "evidence": ""
    },
    {
      "surface": "tool",
      "value": "arbitrary network Shell",
      "status": "unsupported",
      "evidence": ""
    },
    {
      "surface": "replay",
      "value": "remote runner",
      "status": "unsupported",
      "evidence": ""
    }
  ]
}
```

The actual release validator checks this block before a release artifact is
published. Real Codex installation remains a separate human acceptance gate.
