# Security Policy

## Reporting a vulnerability

Report suspected vulnerabilities privately through the repository's configured
GitHub Security Advisory channel or maintainer contact. Do not include
credentials, raw prompts, SQLite files, outbox data, or replay payloads in a
public issue. If no private channel is configured for a deployment, stop and
ask its maintainer for a secure contact before sending details.

## Supported security scope

The local Plugin runtime, offline Bundle installer, Hook boundary, memory
approval boundary, and fixture-only replay runner are in scope. The Codex host,
third-party Tools, arbitrary user containers, and unverified remote runners are
outside this repository's security guarantee.

Hook input is untrusted. Replay capsules and fixture artifacts must be treated
as sensitive local data. Hash manifests detect accidental modification; this
development release does not yet provide cryptographic release signatures.

## Disclosure

Do not publish an exploit or sensitive evidence until maintainers have agreed
on a remediation and disclosure timeline.
