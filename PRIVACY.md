# Privacy

SkillTree is designed for local, user-controlled operation.

## Data and location

- SQLite runtime state, memory candidates, approved Profile/Procedure records,
  audit rows, and sanitized outbox entries live under the configured Plugin
  data directory, not the workspace or Skill root.
- ReplayCapsule metadata and reports are local governed artifacts. OCI images
  belong to the separately installed replay extension, not the base Plugin.
- Raw prompts, credentials, token values, and transcript files are not stored
  by the release Bundle.

## Control

Memory writes require explicit user approval. Export, hide, purge, and clear
operations are explicit governance actions. Retention and deletion behavior is
defined by the active schema and lifecycle service.

The release validation process must not package SQLite files, outbox content,
prompts, credentials, replay blobs, or sensitive Hook fixtures. CI reports are
redacted and contain only bounded hashes, paths within the staged artifact,
versions, and diagnostic codes.
