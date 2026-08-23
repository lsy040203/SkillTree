from __future__ import annotations

import pytest

from skilltree.core.secret_protector import SecretProtectionError, protect, unprotect


def test_dpapi_round_trip_or_explicitly_reports_unavailable() -> None:
    try:
        encrypted = protect(b"fixture")
    except SecretProtectionError as error:
        assert str(error) == "dpapi_unavailable"
        return
    assert encrypted != b"fixture"
    assert unprotect(encrypted) == b"fixture"
