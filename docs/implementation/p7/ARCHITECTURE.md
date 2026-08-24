# P7 Release Foundation Architecture

## Status

P7.1/P7.2 are implemented. P7.3 signing/key governance tooling is implemented
as a detached `cosign` boundary. P7.4 real Codex release acceptance remains a
human gate and is not claimed by CI.

```text
source + locked dependencies
            |
            v
      build_bundle.py
            |
            v
     staged release Bundle
            |
            +--> Core manifest validator
            +--> release validator / forbidden-file policy
            +--> deterministic SBOM
            +--> compatibility matrix checker
            v
       CI validation artifacts
```

The existing `skilltree.core.bundle` code remains authoritative for Plugin/Core
version matching, migration continuity, wheel and lock hashes, runtime file
coverage, Hook bundle coverage, and the P0 manifest hash. The release-facing
validator adds staging and publication policy without weakening those checks.

## Bundle boundary

The base Bundle contains only Plugin skills, hooks, migrations, runtime files,
locked wheels, and manifests. It excludes source trees, tests and fixtures,
`.venv`, SQLite/outbox data, Replay blobs and OCI archives, credentials, and
other development artifacts.

## Evidence boundary

CI emits stable JSON reports with versions, bounded relative paths, hashes, and
diagnostic codes. It never emits raw prompt text, credentials, tokens, SQLite
content, or replay payloads. A successful CI run is not proof of real Codex
installation compatibility; that remains a P7.4 human Gate.
