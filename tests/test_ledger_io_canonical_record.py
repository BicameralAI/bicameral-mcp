"""Functional tests for canonical-record shape + frozenset parity (#252 Layer 4)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cli.ledger_io import (
    _DATA_TABLES,
    _DELETE_BEFORE_IMPORT,
    _EDGE_TABLES,
    EXPORT_RECORD_VERSION,
    ExportError,
    _canonical_record,
    _record_sort_key,
)


def test_canonical_record_stamps_table_schema_version_record_version():
    out = _canonical_record("decision", {"id": "decision:abc", "description": "x"}, 16)
    assert out["_table"] == "decision"
    assert out["_schema_version"] == 16
    assert out["_record_version"] == EXPORT_RECORD_VERSION
    assert out["id"] == "decision:abc"
    assert out["description"] == "x"


def test_canonical_record_does_not_mutate_input():
    src = {"id": "decision:abc", "description": "x"}
    snapshot = dict(src)
    _canonical_record("decision", src, 16)
    assert src == snapshot


def test_canonical_record_raises_on_reserved_field_name_collision():
    with pytest.raises(ExportError):
        _canonical_record("decision", {"id": "x", "_table": "evil"}, 16)


def test_data_and_edge_table_sets_cover_all_schema_define_table_statements():
    """Read schema.py, grep all DEFINE TABLE <name>, assert membership."""
    schema_path = Path(__file__).resolve().parent.parent / "ledger" / "schema.py"
    text = schema_path.read_text(encoding="utf-8")
    # Match `DEFINE TABLE <name>` (excluding OVERWRITE clause word).
    pattern = re.compile(r'"DEFINE TABLE (?:OVERWRITE\s+)?([a-z_]+)\b')
    schema_tables = set(pattern.findall(text))
    covered = _DATA_TABLES | _EDGE_TABLES
    missing = schema_tables - covered
    assert not missing, f"tables in schema.py not covered by _DATA_TABLES|_EDGE_TABLES: {missing}"


def test_data_and_edge_table_sets_are_disjoint():
    assert _DATA_TABLES.isdisjoint(_EDGE_TABLES)


def test_delete_before_import_subset_of_data_tables():
    assert _DELETE_BEFORE_IMPORT.issubset(_DATA_TABLES)


def test_delete_before_import_includes_meta_tables():
    assert "bicameral_meta" in _DELETE_BEFORE_IMPORT
    assert "schema_meta" in _DELETE_BEFORE_IMPORT


def test_record_sort_key_orders_by_table_then_created_at_then_id():
    a = {"_table": "decision", "created_at": "2026-05-07T10:00:00Z", "id": "decision:a"}
    b = {"_table": "decision", "created_at": "2026-05-07T11:00:00Z", "id": "decision:b"}
    c = {"_table": "binds_to", "created_at": "2026-05-07T09:00:00Z", "id": "binds_to:c"}
    sorted_recs = sorted([a, b, c], key=_record_sort_key)
    assert [r["id"] for r in sorted_recs] == ["binds_to:c", "decision:a", "decision:b"]


def test_export_record_version_is_pinned_int():
    assert EXPORT_RECORD_VERSION == 1
