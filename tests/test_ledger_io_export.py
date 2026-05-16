"""Functional tests for export_jsonl() (#252 Layer 4 Phase 1)."""

from __future__ import annotations

import json

import pytest

from cli._ledger_io_engine import export_jsonl
from cli.ledger_io import _DATA_TABLES, _EDGE_TABLES
from ledger.adapter import SurrealDBLedgerAdapter


@pytest.fixture
async def adapter():
    a = SurrealDBLedgerAdapter("memory://")
    await a.connect()
    yield a
    await a._client.close()


async def _collect(gen) -> list[str]:
    out: list[str] = []
    async for line in gen:
        out.append(line)
    return out


async def test_export_jsonl_emits_only_meta_records_for_fresh_ledger(adapter):
    lines = await _collect(export_jsonl(adapter))
    # Fresh memory:// ledger has Layer 2's bicameral_meta + schema_meta
    # auto-populated at connect, so 2 records expected.
    assert len(lines) == 2
    tables = sorted(json.loads(line)["_table"] for line in lines)
    assert tables == ["bicameral_meta", "schema_meta"]


async def test_export_jsonl_emits_data_tables_before_edge_tables(adapter):
    # Insert one decision (data) + manually relate (edge requires existing data).
    await adapter._client.query(
        "CREATE decision:test1 SET description = $d, status = 'ungrounded', canonical_id = 'k1'",
        {"d": "test"},
    )
    await adapter._client.query("CREATE input_span:test1 SET text = 't', source_type = 'manual'")
    await adapter._client.query(
        "RELATE input_span:test1->yields->decision:test1 SET created_at = time::now()"
    )
    lines = await _collect(export_jsonl(adapter))
    parsed = [json.loads(line) for line in lines]
    table_order = [rec["_table"] for rec in parsed]
    decision_idx = table_order.index("decision")
    yields_idx = table_order.index("yields")
    assert decision_idx < yields_idx, "data tables must precede edge tables"


async def test_export_jsonl_each_record_has_metadata_stamps(adapter):
    lines = await _collect(export_jsonl(adapter))
    for line in lines:
        rec = json.loads(line)
        assert rec["_table"] in (_DATA_TABLES | _EDGE_TABLES)
        assert isinstance(rec["_schema_version"], int)
        assert rec["_record_version"] == 1


async def test_export_jsonl_round_trip_is_deterministic(adapter):
    lines_a = await _collect(export_jsonl(adapter))
    lines_b = await _collect(export_jsonl(adapter))
    assert lines_a == lines_b


async def test_export_jsonl_handles_missing_table_gracefully(monkeypatch, adapter):
    """If a SELECT against a non-existent table errors, _gather_table_rows
    swallows it and the export proceeds for other tables."""
    # All canonical tables exist in memory:// (init_schema applies them);
    # this test verifies the gather function's tolerance via direct call.
    from cli._ledger_io_engine import _gather_table_rows

    rows = await _gather_table_rows(adapter, "no_such_table_xyzzy")
    assert rows == []


async def test_export_jsonl_uses_schema_meta_version_when_present(adapter):
    lines = await _collect(export_jsonl(adapter))
    schema_meta_rec = next(
        json.loads(line) for line in lines if json.loads(line)["_table"] == "schema_meta"
    )
    # Layer 2's migrate populates schema_meta.version = SCHEMA_VERSION
    from ledger.schema import SCHEMA_VERSION

    assert schema_meta_rec["_schema_version"] == SCHEMA_VERSION


async def test_export_jsonl_records_sorted_by_table_then_created_at_then_id(adapter):
    """Insert three decisions with distinct created_at timestamps in non-order;
    assert export sorts by created_at ascending."""
    await adapter._client.query(
        "CREATE decision:a SET description = 'a', status = 'ungrounded', canonical_id = 'a', "
        "created_at = type::datetime('2026-05-07T11:00:00Z')"
    )
    await adapter._client.query(
        "CREATE decision:b SET description = 'b', status = 'ungrounded', canonical_id = 'b', "
        "created_at = type::datetime('2026-05-07T09:00:00Z')"
    )
    await adapter._client.query(
        "CREATE decision:c SET description = 'c', status = 'ungrounded', canonical_id = 'c', "
        "created_at = type::datetime('2026-05-07T10:00:00Z')"
    )
    lines = await _collect(export_jsonl(adapter))
    decisions = [json.loads(line) for line in lines if json.loads(line)["_table"] == "decision"]
    ids_in_order = [d["id"] for d in decisions]
    # Expected order by created_at: b (09:00), c (10:00), a (11:00)
    assert ids_in_order == ["decision:b", "decision:c", "decision:a"]
