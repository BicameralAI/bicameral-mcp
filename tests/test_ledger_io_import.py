"""Functional tests for import_jsonl() (#252 Layer 4 Phase 1)."""

from __future__ import annotations

import json

import pytest

from cli._ledger_io_engine import export_jsonl, import_jsonl
from cli.ledger_io import ImportError_, ImportSummary
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


async def test_import_jsonl_round_trip_preserves_meta_table_singletons(adapter):
    """Path B contract: post-import, each meta table has exactly 1 row."""
    lines = await _collect(export_jsonl(adapter))
    summary = await import_jsonl(adapter, lines)
    assert summary.data_records_written.get("bicameral_meta") == 1
    assert summary.data_records_written.get("schema_meta") == 1
    bm = await adapter._client.query("SELECT count() AS n FROM bicameral_meta GROUP ALL")
    sm = await adapter._client.query("SELECT count() AS n FROM schema_meta GROUP ALL")
    assert bm[0]["n"] == 1
    assert sm[0]["n"] == 1


async def test_import_jsonl_returns_import_summary_with_per_table_counts(adapter):
    lines = await _collect(export_jsonl(adapter))
    summary = await import_jsonl(adapter, lines)
    assert isinstance(summary, ImportSummary)
    assert summary.total_records_written == sum(summary.data_records_written.values()) + sum(
        summary.edge_records_written.values()
    )


async def test_import_jsonl_fails_fast_on_non_empty_ledger(adapter):
    # Populate a non-meta table.
    await adapter._client.query(
        "CREATE decision:dirty SET description = 'x', status = 'ungrounded', canonical_id = 'd1'"
    )
    with pytest.raises(ImportError_, match="non-empty"):
        await import_jsonl(adapter, [])


async def test_import_jsonl_fails_fast_on_unknown_table(adapter):
    bad = json.dumps(
        {"_table": "evil_unknown", "_schema_version": 16, "_record_version": 1, "id": "x:y"}
    )
    with pytest.raises(ImportError_, match="unknown _table"):
        await import_jsonl(adapter, [bad])


async def test_import_jsonl_fails_fast_on_record_version_mismatch(adapter):
    bad = json.dumps(
        {"_table": "decision", "_schema_version": 16, "_record_version": 999, "id": "decision:x"}
    )
    with pytest.raises(ImportError_, match="_record_version"):
        await import_jsonl(adapter, [bad])


async def test_import_jsonl_fails_fast_on_schema_version_too_new(adapter):
    bad = json.dumps(
        {"_table": "decision", "_schema_version": 99, "_record_version": 1, "id": "decision:x"}
    )
    with pytest.raises(ImportError_, match="_schema_version"):
        await import_jsonl(adapter, [bad])


async def test_import_jsonl_fails_fast_on_missing_id(adapter):
    bad = json.dumps({"_table": "decision", "_schema_version": 16, "_record_version": 1})
    with pytest.raises(ImportError_, match="missing required `id`"):
        await import_jsonl(adapter, [bad])


async def test_import_jsonl_validation_phase_collects_all_errors(adapter):
    """Operator gets the FULL error list, not first-error-only."""
    bad1 = json.dumps({"_table": "evil1", "_schema_version": 16, "_record_version": 1, "id": "x:1"})
    bad2 = json.dumps(
        {"_table": "decision", "_schema_version": 999, "_record_version": 1, "id": "x:2"}
    )
    bad3 = json.dumps({"_table": "decision", "_schema_version": 16, "_record_version": 1})
    with pytest.raises(ImportError_) as exc_info:
        await import_jsonl(adapter, [bad1, bad2, bad3])
    msg = str(exc_info.value)
    assert "evil1" in msg  # first error
    assert "_schema_version" in msg  # second error
    assert "missing required" in msg  # third error


async def test_import_jsonl_delete_before_import_preserves_source_at_first_write_provenance(
    adapter,
):
    """Path B core contract: source's at_first_write survives the round-trip."""
    # Manually rewrite destination's bicameral_meta to simulate "destination version".
    await adapter._client.query(
        "UPDATE bicameral_meta SET surrealdb_client_version_at_first_write = 'dest-version'"
    )
    # Build source JSONL with different at_first_write.
    source_lines = [
        json.dumps(
            {
                "_table": "bicameral_meta",
                "_schema_version": 16,
                "_record_version": 1,
                "id": "bicameral_meta:source_id_xyz",
                "surrealdb_client_version_at_first_write": "source-version",
                "surrealdb_client_version_at_last_write": "source-version",
            }
        ),
        json.dumps(
            {
                "_table": "schema_meta",
                "_schema_version": 16,
                "_record_version": 1,
                "id": "schema_meta:source_id_xyz",
                "version": 16,
            }
        ),
    ]
    await import_jsonl(adapter, source_lines)
    rows = await adapter._client.query(
        "SELECT surrealdb_client_version_at_first_write FROM bicameral_meta LIMIT 1"
    )
    assert rows[0]["surrealdb_client_version_at_first_write"] == "source-version"


async def test_import_jsonl_delete_before_import_yields_exactly_one_row_per_meta_table(adapter):
    """No duplicate rows after Path B import."""
    source_lines = [
        json.dumps(
            {
                "_table": "bicameral_meta",
                "_schema_version": 16,
                "_record_version": 1,
                "id": "bicameral_meta:src",
                "surrealdb_client_version_at_first_write": "v",
                "surrealdb_client_version_at_last_write": "v",
            }
        ),
        json.dumps(
            {
                "_table": "schema_meta",
                "_schema_version": 16,
                "_record_version": 1,
                "id": "schema_meta:src",
                "version": 16,
            }
        ),
    ]
    await import_jsonl(adapter, source_lines)
    bm = await adapter._client.query("SELECT count() AS n FROM bicameral_meta GROUP ALL")
    sm = await adapter._client.query("SELECT count() AS n FROM schema_meta GROUP ALL")
    assert bm[0]["n"] == 1
    assert sm[0]["n"] == 1


async def test_assert_ledger_empty_skips_delete_before_import_tables(adapter):
    """Round-1 audit advisory: meta tables auto-populated at connect must
    not trip the empty-ledger gate."""
    from cli._ledger_io_engine import _assert_ledger_empty

    # bicameral_meta + schema_meta are populated by connect(), but
    # _assert_ledger_empty must skip them and not raise.
    await _assert_ledger_empty(adapter)  # should NOT raise


async def test_import_jsonl_writes_edge_via_relate(adapter):
    """Phase B step 3 uses RELATE syntax for edge tables."""
    # Set up: data records for the edge endpoints.
    source_lines = [
        json.dumps(
            {
                "_table": "input_span",
                "_schema_version": 16,
                "_record_version": 1,
                "id": "input_span:edge_in",
                "text": "x",
                "source_type": "manual",
            }
        ),
        json.dumps(
            {
                "_table": "decision",
                "_schema_version": 16,
                "_record_version": 1,
                "id": "decision:edge_out",
                "description": "x",
                "status": "ungrounded",
                "canonical_id": "edge_out",
            }
        ),
        json.dumps(
            {
                "_table": "yields",
                "_schema_version": 16,
                "_record_version": 1,
                "id": "yields:edge_id",
                "in": "input_span:edge_in",
                "out": "decision:edge_out",
            }
        ),
    ]
    summary = await import_jsonl(adapter, source_lines)
    assert summary.edge_records_written.get("yields") == 1
    rows = await adapter._client.query("SELECT * FROM yields")
    assert len(rows) == 1
    assert str(rows[0]["in"]) == "input_span:edge_in"
    assert str(rows[0]["out"]) == "decision:edge_out"
