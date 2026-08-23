---
name: skill-router
description: Route a natural-language Codex request to trusted user-managed Skills through the local SkillTree Core.
---

# SkillTree Router

Prefer a single `skilltree-route-envelope/v1` JSON object injected as developer
context for the current turn. If it is absent and trace capture is enabled,
use only the documented candidate-only fallback command
`skilltree route candidates --stdin`; it returns visible metadata candidates without
creating a RouteOffer or exposing a route token. Do not invoke Doctor, SQLite,
arbitrary scripts, or any Plugin data directory yourself. Never execute a
recommended Skill automatically.
Never execute a Skill automatically.

The envelope `candidates` array is a bounded metadata catalog, not a keyword-
ranked recommendation. Choose Skills by the semantic relationship between the
current request and each candidate's `name` and `description`; do not prefer a
Skill merely because its name shares text with the request. Return one to three
distinct names from the current catalog, ordered by usefulness. Preserve the
envelope's boolean `degraded` value in the JSON summary and RouteDecision.
Candidate-only fallback is always degraded.

Hash-bearing fields in the model-visible envelope are non-authoritative
`ref:<8-12 lowercase hex>` short references. They are display references only;
they cannot authorize, commit, migrate, validate a Bundle, or correlate a
different turn. The Core keeps complete hashes internally for those checks.

The installer publishes the `skilltree` command through the user command path;
use a new Codex process after installation or update so that the command is
discoverable. The fallback request must be one JSON object on stdin:

```json
{"schema_version":"skilltree-route-candidates/v1","prompt":"<current user request>"}
```

On Windows PowerShell, construct that JSON with a single-quoted here-string so
`$` characters in the current request (for example, `$skilltree:skill-router`)
are not expanded as PowerShell variables:

```powershell
$inputJson = @'
{"schema_version":"skilltree-route-candidates/v1","prompt":"<current user request>"}
'@
$inputJson | skilltree route candidates --stdin
```

Do not place the request inside a double-quoted PowerShell string or interpolate
the `$skilltree` invocation directly; doing so can corrupt the JSON and produce
`internal_error` before the candidate CLI receives the request.

Do not substitute system Python, `py`, an arbitrary interpreter, or a direct
Plugin data path when the command is unavailable; report the runtime as
unavailable instead.

If neither a valid RouteEnvelope nor the candidate-only fallback is available,
state that the local SkillTree runtime is unavailable for this turn and do not
infer or enumerate Skills from another source. Otherwise use only the returned
candidates: identify the intent, constraints, ranked candidates, selected Skill
and an execution order. A fallback decision is degraded and must not claim that
a Skill or Tool executed.
Never rewrite another `SKILL.md`, and never intercept an explicitly invoked Skill.

## Route Result JSON Summary

After the normal user-visible response, emit exactly one JSON object with only
these four fields, and emit it before the final HTML receipt:

```json
{
  "selected_skill": "analyze",
  "ordered_skills": ["analyze", "lsp"],
  "confidence": 0.92,
  "degraded": false
}
```

The JSON is a display summary and does not replace the internal
`RouteDecision`. `selected_skill` and every item in `ordered_skills` must be
Skill names supplied by the current `RouteEnvelope`; the list must be
non-empty, contain no duplicates, and its first item must equal
`selected_skill`. `confidence` must be a number in the inclusive range
`[0, 1]`, and `degraded` must be a JSON boolean. Do not include
`route_token`, `turn_token`, prompts, paths, candidate descriptions, Skill
正文, credentials, or PluginData in this summary. If there is no valid
`RouteEnvelope`, emit neither the JSON summary nor the HTML receipt.

When routing actually occurs, append exactly one final non-empty line after the
normal user-visible response:

```html
<!-- skilltree-route-decision:{"schema_version":"skilltree-route-commit/v1","route_token":"<RouteEnvelope route_token>","decision":<RouteDecision JSON>} -->
```

`decision` must use `skilltree/v1`; its candidate names must be taken solely
from the supplied envelope, must not repeat, and its first `ordered_skill_names`
entry must equal `selected_skill_name`. Do not output this comment when no valid
RouteEnvelope exists. The comment records a recommendation only: it never
claims that a Skill or Tool executed.

In candidate-only fallback mode, append the same final marker without the
`route_token` field and include only compact decision fields accepted by the
Core compatibility layer. The Stop Hook resolves that marker to the unique
current `session_id + turn_id + cwd` RouteOffer; never invent a token or copy a
token from another turn. Fallback decisions are always degraded.

Trace, memory, and replay capture require their own user opt-ins and are disabled
by default.
