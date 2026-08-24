from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.sign_release import (
    SignatureError,
    build_signature_metadata,
    validate_keyring,
    validate_signature_key_binding,
    validate_signature_paths,
    verify_signature_metadata,
)


def test_signature_metadata_contains_only_public_release_facts(tmp_path: Path) -> None:
    artifact = tmp_path / "plugin.zip"
    signature = tmp_path / "plugin.zip.sig"
    public_key = tmp_path / "release.pub"
    artifact.write_bytes(b"release")
    signature.write_bytes(b"detached-signature")
    public_key.write_text("PUBLIC KEY", encoding="utf-8")

    metadata = build_signature_metadata(
        artifact=artifact,
        signature=signature,
        public_key=public_key,
        key_id="skilltree-release-2026-01",
    )

    assert metadata["schema_version"] == "skilltree-release-signature/v1"
    assert metadata["artifact"] == "plugin.zip"
    assert metadata["signature"] == "plugin.zip.sig"
    assert metadata["public_key"] == "release.pub"
    assert metadata["key_id"] == "skilltree-release-2026-01"
    assert "private" not in json.dumps(metadata).lower()


def test_signature_metadata_rejects_changed_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "plugin.zip"
    signature = tmp_path / "plugin.zip.sig"
    public_key = tmp_path / "release.pub"
    artifact.write_bytes(b"release")
    signature.write_bytes(b"sig")
    public_key.write_text("PUBLIC KEY", encoding="utf-8")
    metadata = build_signature_metadata(
        artifact=artifact, signature=signature, public_key=public_key, key_id="key-1"
    )
    artifact.write_bytes(b"tampered")

    with pytest.raises(SignatureError, match="artifact_hash_mismatch"):
        verify_signature_metadata(metadata, tmp_path)


def test_signature_paths_must_match_hashed_release_assets(tmp_path: Path) -> None:
    artifact = tmp_path / "plugin.zip"
    signature = tmp_path / "plugin.zip.sig"
    public_key = tmp_path / "release.pub"
    artifact.write_bytes(b"release")
    signature.write_bytes(b"sig")
    public_key.write_text("PUBLIC KEY", encoding="utf-8")
    metadata = build_signature_metadata(
        artifact=artifact, signature=signature, public_key=public_key, key_id="key-1"
    )

    with pytest.raises(SignatureError, match="metadata_invalid"):
        validate_signature_paths(
            metadata,
            tmp_path,
            artifact=tmp_path / "other.zip",
            signature=signature,
            public_key=public_key,
        )


def test_keyring_requires_one_active_key_and_allows_retirement() -> None:
    validate_keyring({
        "schema_version": "skilltree-release-keyring/v1",
        "active_key_id": "key-2",
        "keys": [
            {"key_id": "key-1", "public_key": "release-key-1.pub", "status": "retired"},
            {"key_id": "key-2", "public_key": "release-key-2.pub", "status": "active"},
        ],
    })


def test_keyring_rejects_two_active_keys() -> None:
    with pytest.raises(SignatureError, match="keyring_invalid"):
        validate_keyring({
            "schema_version": "skilltree-release-keyring/v1",
            "active_key_id": "key-1",
            "keys": [
                {"key_id": "key-1", "public_key": "one.pub", "status": "active"},
                {"key_id": "key-2", "public_key": "two.pub", "status": "active"},
            ],
        })


def test_keyring_active_id_must_reference_active_key() -> None:
    with pytest.raises(SignatureError, match="keyring_invalid"):
        validate_keyring({
            "schema_version": "skilltree-release-keyring/v1",
            "active_key_id": "key-1",
            "keys": [
                {"key_id": "key-1", "public_key": "one.pub", "status": "retired"},
                {"key_id": "key-2", "public_key": "two.pub", "status": "active"},
            ],
        })


def test_signature_key_binding_accepts_retired_key_for_existing_release() -> None:
    metadata = {"key_id": "key-1", "public_key": "release-key-1.pub"}
    keyring = {
        "schema_version": "skilltree-release-keyring/v1",
        "active_key_id": "key-2",
        "keys": [
            {"key_id": "key-1", "public_key": "release-key-1.pub", "status": "retired"},
            {"key_id": "key-2", "public_key": "release-key-2.pub", "status": "active"},
        ],
    }

    validate_signature_key_binding(metadata, keyring)


@pytest.mark.parametrize(
    "metadata,keyring",
    [
        (
            {"key_id": "unknown", "public_key": "release.pub"},
            {
                "schema_version": "skilltree-release-keyring/v1",
                "active_key_id": "active",
                "keys": [{"key_id": "active", "public_key": "active.pub", "status": "active"}],
            },
        ),
        (
            {"key_id": "key-1", "public_key": "wrong.pub"},
            {
                "schema_version": "skilltree-release-keyring/v1",
                "active_key_id": "key-1",
                "keys": [{"key_id": "key-1", "public_key": "release.pub", "status": "active"}],
            },
        ),
        (
            {"key_id": "key-1", "public_key": "release.pub"},
            {
                "schema_version": "skilltree-release-keyring/v1",
                "active_key_id": "key-1",
                "keys": [{"key_id": "key-1", "public_key": "release.pub", "status": "revoked"}],
            },
        ),
    ],
)
def test_signature_key_binding_rejects_unknown_mismatch_or_revoked_key(metadata, keyring) -> None:
    with pytest.raises(SignatureError, match="keyring_invalid"):
        validate_signature_key_binding(metadata, keyring)
