# Contributing to SkillTree

## Before opening a change

Run the focused tests for the files you changed and then the full regression
suite. Keep changes scoped to the approved phase and document any environment
limitations. Do not claim real Codex compatibility from host-neutral fixtures.

## Prohibited repository content

Never commit real user prompts or transcripts, API keys or other credentials,
SQLite databases, outbox data, ReplayCapsule payloads or OCI archives, or Hook
fixtures containing sensitive output. Use synthetic fixtures and redact reports.

Do not add code that automatically executes arbitrary Shell commands, modifies
`SKILL.md`, approves memory candidates, or publishes a release without an
explicit human gate.

## Pull requests

Describe the exact tests and environment used. Release changes must include
deterministic Bundle, lock/hash, validator, and SBOM evidence. CI passing does
not replace the separate real-Codex acceptance record.
