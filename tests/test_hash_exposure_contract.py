from __future__ import annotations

import json
from datetime import UTC, datetime

from skilltree.hook_bridge import (
    build_probe,
    project_route_envelope,
    public_hash_ref,
    validate_internal_route_envelope,
    validate_public_route_envelope,
    write_probe,
)
from skilltree.hook_bridge import HookInput


INTERNAL_ENVELOPE = {
    "schema_version": "skilltree-route-envelope/v1",
    "route_token": "a" * 43,
    "expires_at": "2026-08-15T00:05:00.000Z",
    "candidate_snapshot_hash": "sha256:" + "b" * 64,
    "degraded": False,
    "candidates": [{
        "name": "analyze",
        "description": "Analyze repositories",
        "content_hash": "sha256:" + "c" * 64,
    }],
}


def test_public_hash_reference_is_short_and_non_authoritative() -> None:
    reference = public_hash_ref("sha256:" + "a" * 64)

    assert reference == "ref:" + "a" * 12
    assert len(reference) == 16
    assert reference != "sha256:" + "a" * 64


def test_public_projection_hides_complete_hashes() -> None:
    projected = project_route_envelope(INTERNAL_ENVELOPE)
    serialized = json.dumps(projected, sort_keys=True)

    assert validate_internal_route_envelope(INTERNAL_ENVELOPE)
    assert validate_public_route_envelope(projected)
    assert "sha256:" + "b" * 64 not in serialized
    assert "sha256:" + "c" * 64 not in serialized
    assert projected["candidate_snapshot_hash"] == "ref:" + "b" * 12
    assert projected["candidates"][0]["content_hash"] == "ref:" + "c" * 12


def test_public_reference_is_not_accepted_as_internal_hash() -> None:
    malformed = dict(INTERNAL_ENVELOPE, candidate_snapshot_hash="ref:123456789abc")

    assert not validate_internal_route_envelope(malformed)


def test_probe_file_redacts_known_hash_fields(tmp_path) -> None:
    event = HookInput("Stop", {"session_id": "session", "cwd": "C:/work", "last_assistant_message": None})
    probe = build_probe(event, "sha256:" + "d" * 64, datetime.now(UTC))

    write_probe(tmp_path, probe, datetime.now(UTC))
    files = list((tmp_path / "diagnostics" / "g0.25").glob("*.json"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "sha256:" + "d" * 64 not in text
    payload = json.loads(text)
    assert payload["hook_bundle_hash"] == "ref:" + "d" * 12
    assert payload["shape_hash"].startswith("ref:")
