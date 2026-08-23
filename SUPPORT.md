# Support and compatibility

This matrix describes the current release-foundation claim, not a guarantee
for untested environments.

| Surface | Supported baseline | Unsupported or not yet claimed | Evidence |
|---|---|---|---|
| OS | Windows PowerShell development/runtime path | Other OS release support | Existing local test suite |
| Python | 3.11+ as declared by `pyproject.toml` | Older Python versions | `pyproject.toml` |
| Codex | Plugin/Hook shape covered by repository fixtures | New host fields or unverified desktop versions | `docs/verification/G0.25-hook-context-output.md` |
| Hook | `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop` fixtures | Hosted Tools not present in the validated fixture contract | Hook contract tests |
| Tool execution | Observed, registered local Tool summaries and fixture-only replay | Arbitrary Shell, network fetches, privileged containers | `DEV_SPEC.md` replay guardrails |
| Replay | Installed extension with paired baseline/candidate fixture runs | Remote runners and arbitrary project adapters | `docs/implementation/p6/PRODUCTION_ACCEPTANCE.md` |

Real Codex installation and release behavior require a separate human
acceptance record; CI and host-neutral fixtures do not replace it.
