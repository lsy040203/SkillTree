"""Sign and verify detached Release assets with an external cosign binary.

Private keys are accepted only as command arguments to cosign and are never
read into metadata or persisted by this module. The repository may distribute
the resulting public key and signature metadata, but it must not contain a
private key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = "skilltree-release-signature/v1"
KEYRING_SCHEMA_VERSION = "skilltree-release-keyring/v1"


class SignatureError(ValueError):
    """Raised when a detached signature or public key contract is invalid."""


def build_signature_metadata(
    *, artifact: Path, signature: Path, public_key: Path, key_id: str
) -> dict[str, Any]:
    """Create public, content-addressed metadata for one signed artifact."""
    if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", key_id):
        raise SignatureError("invalid_key_id")
    for path in (artifact, signature, public_key):
        if not path.is_file():
            raise SignatureError("asset_missing")
    return {
        "schema_version": SCHEMA_VERSION,
        "algorithm": "cosign-blob/v1",
        "artifact": artifact.name,
        "artifact_sha256": _sha256(artifact),
        "signature": signature.name,
        "signature_sha256": _sha256(signature),
        "public_key": public_key.name,
        "public_key_sha256": _sha256(public_key),
        "key_id": key_id,
    }


def verify_signature_metadata(metadata: dict[str, Any], directory: Path) -> None:
    """Verify metadata shape and all local content hashes before cosign verify."""
    required = {
        "schema_version", "algorithm", "artifact", "artifact_sha256",
        "signature", "signature_sha256", "public_key", "public_key_sha256", "key_id",
    }
    if not isinstance(metadata, dict) or set(metadata) != required:
        raise SignatureError("metadata_invalid")
    if metadata["schema_version"] != SCHEMA_VERSION or metadata["algorithm"] != "cosign-blob/v1":
        raise SignatureError("metadata_invalid")
    for field in ("artifact", "signature", "public_key"):
        value = metadata[field]
        if not isinstance(value, str) or not value or Path(value).name != value:
            raise SignatureError("metadata_invalid")
    for field, filename in (
        ("artifact_sha256", metadata["artifact"]),
        ("signature_sha256", metadata["signature"]),
        ("public_key_sha256", metadata["public_key"]),
    ):
        path = directory / filename
        if not path.is_file() or _sha256(path) != metadata[field]:
            raise SignatureError(field.replace("_sha256", "_hash_mismatch"))
    if not isinstance(metadata["key_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", metadata["key_id"]):
        raise SignatureError("metadata_invalid")


def validate_keyring(keyring: dict[str, Any]) -> None:
    """Validate public-key distribution and rotation metadata."""
    required = {"schema_version", "active_key_id", "keys"}
    if not isinstance(keyring, dict) or set(keyring) != required:
        raise SignatureError("keyring_invalid")
    if keyring["schema_version"] != KEYRING_SCHEMA_VERSION:
        raise SignatureError("keyring_invalid")
    keys = keyring["keys"]
    if not isinstance(keys, list) or not keys:
        raise SignatureError("keyring_invalid")
    ids: set[str] = set()
    active = keyring["active_key_id"]
    for entry in keys:
        if not isinstance(entry, dict) or set(entry) != {"key_id", "public_key", "status"}:
            raise SignatureError("keyring_invalid")
        key_id = entry["key_id"]
        public_key = entry["public_key"]
        status = entry["status"]
        if not isinstance(key_id, str) or not key_id or key_id in ids:
            raise SignatureError("keyring_invalid")
        if not isinstance(public_key, str) or Path(public_key).name != public_key:
            raise SignatureError("keyring_invalid")
        if status not in {"active", "retired", "revoked"}:
            raise SignatureError("keyring_invalid")
        ids.add(key_id)
    active_entries = [entry for entry in keys if entry["key_id"] == active]
    if active not in ids or len(active_entries) != 1 or active_entries[0]["status"] != "active" or sum(entry["status"] == "active" for entry in keys) != 1:
        raise SignatureError("keyring_invalid")


def validate_signature_key_binding(metadata: dict[str, Any], keyring: dict[str, Any]) -> None:
    """Ensure a signature uses a distributed, non-revoked public key."""
    validate_keyring(keyring)
    key_id = metadata.get("key_id") if isinstance(metadata, dict) else None
    public_key = metadata.get("public_key") if isinstance(metadata, dict) else None
    entry = next((item for item in keyring["keys"] if item["key_id"] == key_id), None)
    if entry is None or entry["status"] == "revoked" or entry["public_key"] != public_key:
        raise SignatureError("keyring_invalid")


def validate_signature_paths(
    metadata: dict[str, Any],
    directory: Path,
    *,
    artifact: Path,
    signature: Path,
    public_key: Path,
) -> None:
    """Bind CLI asset arguments to the exact files hashed by metadata."""
    verify_signature_metadata(metadata, directory)
    for field, path in (("artifact", artifact), ("signature", signature), ("public_key", public_key)):
        try:
            relative = path.resolve().relative_to(directory.resolve())
        except ValueError:
            raise SignatureError("metadata_invalid") from None
        if relative.parts != (metadata[field],):
            raise SignatureError("metadata_invalid")


def sign_with_cosign(*, artifact: Path, signature: Path, private_key: Path, cosign: str = "cosign") -> None:
    """Create a detached cosign blob signature without persisting the key."""
    if not artifact.is_file() or not private_key.is_file():
        raise SignatureError("asset_missing")
    # Cosign v3 uses a Sigstore bundle as the detached verification artifact.
    _run([cosign, "sign-blob", "--yes", "--key", str(private_key), "--bundle", str(signature), str(artifact)])


def verify_with_cosign(*, artifact: Path, signature: Path, public_key: Path, cosign: str = "cosign") -> None:
    if not all(path.is_file() for path in (artifact, signature, public_key)):
        raise SignatureError("asset_missing")
    _run([cosign, "verify-blob", "--key", str(public_key), "--bundle", str(signature), str(artifact)])


def _run(command: Sequence[str]) -> None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        raise SignatureError("signing_tool_unavailable") from None
    if result.returncode != 0:
        raise SignatureError("signature_operation_failed")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    sign = subparsers.add_parser("sign")
    sign.add_argument("--artifact", type=Path, required=True)
    sign.add_argument("--signature", type=Path, required=True)
    sign.add_argument("--private-key", type=Path, required=True)
    sign.add_argument("--public-key", type=Path, required=True)
    sign.add_argument("--key-id", required=True)
    sign.add_argument("--cosign", default="cosign")
    sign.add_argument("--metadata", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--metadata", type=Path, required=True)
    verify.add_argument("--directory", type=Path, required=True)
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--signature", type=Path, required=True)
    verify.add_argument("--public-key", type=Path, required=True)
    verify.add_argument("--keyring", type=Path, required=True)
    verify.add_argument("--cosign", default="cosign")
    args = parser.parse_args(argv)
    try:
        if args.command == "sign":
            sign_with_cosign(artifact=args.artifact, signature=args.signature, private_key=args.private_key, cosign=args.cosign)
            metadata = build_signature_metadata(artifact=args.artifact, signature=args.signature, public_key=args.public_key, key_id=args.key_id)
            args.metadata.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            metadata = json.loads(args.metadata.read_text(encoding="utf-8-sig"))
            validate_signature_paths(
                metadata,
                args.directory,
                artifact=args.artifact,
                signature=args.signature,
                public_key=args.public_key,
            )
            keyring = json.loads(args.keyring.read_text(encoding="utf-8-sig"))
            validate_signature_key_binding(metadata, keyring)
            verify_with_cosign(artifact=args.artifact, signature=args.signature, public_key=args.public_key, cosign=args.cosign)
    except (OSError, json.JSONDecodeError, SignatureError) as error:
        print(getattr(error, "args", ["signature_operation_failed"])[0], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
