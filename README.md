# SkillTree

SkillTree is a privacy-first local Codex Plugin for trusted Skill routing,
auditable traces, authorized memory, and controlled replay evaluation.

## Current release boundary

The Plugin owns local runtime metadata and SQLite state under the configured
Plugin data directory. It does not replace the Codex agent loop, execute
arbitrary commands, automatically edit `SKILL.md`, or publish Skills.

Runtime installation is designed for offline use from the bundled, hash-locked
wheels. The current repository is a development checkout until a release
Bundle passes the release validator and the separate real-Codex human gate.

## Privacy and safety

Raw prompts, credentials, tokens, SQLite contents, outbox payloads, and replay
artifacts are not release inputs and must not be committed. Memory candidates
require explicit approval before authoritative storage. See `PRIVACY.md`,
`SECURITY.md`, and `CONTRIBUTING.md`.

## Development

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
```

The Apache-2.0 license applies to this repository. No signed release or
automatic GitHub Release publication is claimed by this development checkout.
