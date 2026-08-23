from __future__ import annotations

from skilltree.core.routing_catalog import build_metadata_catalog


def test_catalog_keeps_all_rows_in_name_order() -> None:
    catalog = build_metadata_catalog(
        [
            {"name": "zeta", "description": "Z", "content_hash": "sha256:z"},
            {"name": "analyze", "description": "A", "content_hash": "sha256:a"},
        ]
    )

    assert catalog.degraded is False
    assert [item["name"] for item in catalog.candidates] == ["analyze", "zeta"]


def test_catalog_truncates_utf8_without_split() -> None:
    catalog = build_metadata_catalog(
        [{"name": "zh", "description": "中" * 200, "content_hash": "sha256:zh"}]
    )

    value = catalog.candidates[0]["description"]
    assert len(value.encode("utf-8")) <= 256
    assert value.endswith("中")


def test_catalog_reports_overflow_as_degraded() -> None:
    rows = [
        {
            "name": "skill-" + str(index),
            "description": "x" * 256,
            "content_hash": "sha256:" + str(index),
        }
        for index in range(500)
    ]

    catalog = build_metadata_catalog(rows)

    assert catalog.degraded is True
    assert catalog.candidates == []
