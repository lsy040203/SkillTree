# P7.3 Release Signing and Key Governance

P7.3 signs a finalized Release asset after Bundle validation, SBOM generation,
and compatibility checks. Signing is detached and performed by an external
`cosign` installation. Private keys never belong in this repository, CI logs,
GitHub Actions artifacts, or the Plugin Bundle.

## Artifact contract

For each asset, publish:

```text
asset.zip
asset.zip.sigstore.json
asset.zip.signature.json
release-keyring.json
release-key-<key-id>.pub
```

`tools/sign_release.py` creates and verifies the detached signature metadata.
The metadata records only the asset digest, Sigstore bundle digest, public-key
digest, algorithm, and key identifier. It does not record a private-key path or
secret value.

## Keyring and rotation

The public keyring uses `skilltree-release-keyring/v1` and must contain exactly
one `active` key. Previous keys move to `retired` after a rotation; compromised
keys move to `revoked` and must never verify new releases. A rotation must
publish the new public key before signing with its key identifier.

Example verification:

```powershell
python tools/sign_release.py verify `
  --metadata skilltree-plugin-v0.4.1.signature.json `
  --directory . `
  --artifact skilltree-plugin-v0.4.1.zip `
  --signature skilltree-plugin-v0.4.1.zip.sigstore.json `
  --public-key release-key-2026-01.pub `
  --keyring release-keyring.json
```

The command requires `cosign` on `PATH` and fails closed on any digest,
metadata, keyring, key-binding, path, or signature mismatch. The three asset
arguments must be the exact files named inside the metadata directory. Retired
keys may verify already-published releases; revoked keys are rejected.

## Release gate

P7.3 is not complete merely because a signature file exists. The release record
must include the exact asset digest, key identifier, public-key distribution
URL, verification output, and key-rotation status. P7.4 real Codex acceptance
must pass before a signed artifact is labeled a production release.
