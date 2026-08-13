---
name: skill-router
description: Route a natural-language Codex request to trusted user-managed Skills through the local SkillTree Core.
---

# SkillTree Router

Run `skilltree doctor --json` before routing. If the runtime is not ready, report
the local diagnostic and do not read or write trace, memory, weight, or replay data.

When ready, request a Top-K recommendation from the SkillTree Core. Only present
trusted candidates supplied by the Core. Never execute a Skill automatically,
never rewrite another `SKILL.md`, and never intercept an explicitly invoked Skill.

Trace, memory, and replay capture require their own user opt-ins and are disabled
by default.
