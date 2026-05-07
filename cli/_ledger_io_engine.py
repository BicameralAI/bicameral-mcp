"""Async engine for ledger export/import (#252 Layer 4).

Split out of ``cli/ledger_io.py`` per round-1 audit mandate to keep both
modules under the 250-LOC Razor ceiling. Imports constants + dataclass
+ canonical-record helpers from ``cli.ledger_io``.

Decomposition (round-1 audit Razor mandate): ``import_jsonl`` is a
~15-LOC orchestrator over 5 private helpers, each ≤ 40 LOC.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from typing import Any

from cli.ledger_io import (
    _DATA_TABLES,
    _DELETE_BEFORE_IMPORT,
    _EDGE_TABLES,
    EXPORT_RECORD_VERSION,
    ImportError_,
    ImportSummary,
    _canonical_record,
    _record_sort_key,
)


async def _gather_table_rows(adapter, table: str) -> list[dict[str, Any]]:
    """Read all rows from `table`. Tolerates missing tables (returns [])."""
    try:
        rows = await adapter._client.query(f"SELECT * FROM {table}")
    except Exception:  # noqa: BLE001 — missing table is acceptable
        return []
    return rows if rows else []


async def export_jsonl(adapter) -> AsyncIterator[str]:
    """Yield JSON-Lines records for every row in every canonical table.

    Order: data tables first (sorted by name), then edge tables (sorted
    by name). Within each table, records sorted by (table, created_at,
    id). Schema version read from `schema_meta.version` or
    `SCHEMA_VERSION` constant fallback.
    """
    from ledger.schema import SCHEMA_VERSION

    schema_rows = await _gather_table_rows(adapter, "schema_meta")
    schema_version = (
        int(schema_rows[0].get("version", SCHEMA_VERSION)) if schema_rows else SCHEMA_VERSION
    )

    for table in sorted(_DATA_TABLES):
        rows = await _gather_table_rows(adapter, table)
        records = [_canonical_record(table, r, schema_version) for r in rows]
        for record in sorted(records, key=_record_sort_key):
            yield json.dumps(record, sort_keys=True, default=str)

    for table in sorted(_EDGE_TABLES):
        rows = await _gather_table_rows(adapter, table)
        records = [_canonical_record(table, r, schema_version) for r in rows]
        for record in sorted(records, key=_record_sort_key):
            yield json.dumps(record, sort_keys=True, default=str)


def _validate_records(lines: Iterable[str]) -> tuple[list[dict], list[dict]]:
    """Phase A. Parse + validate every line; accumulate ALL errors.

    Validates: ``_table`` ∈ _DATA_TABLES ∪ _EDGE_TABLES;
    ``_schema_version`` ≤ target SCHEMA_VERSION;
    ``_record_version`` ≤ EXPORT_RECORD_VERSION; required ``id`` present.

    Returns (data_records, edge_records) on success. Raises
    ``ImportError_`` with multi-line summary on any failure (operator
    gets the FULL list, not first-failure-only).
    """
    from ledger.schema import SCHEMA_VERSION

    errors: list[str] = []
    data_recs: list[dict] = []
    edge_recs: list[dict] = []
    for idx, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {idx}: invalid JSON ({exc})")
            continue
        table = rec.get("_table")
        if table not in _DATA_TABLES and table not in _EDGE_TABLES:
            errors.append(f"line {idx}: unknown _table {table!r}")
            continue
        sv = rec.get("_schema_version")
        if not isinstance(sv, int) or sv > SCHEMA_VERSION:
            errors.append(
                f"line {idx} (table {table!r}): _schema_version {sv!r} > target {SCHEMA_VERSION}"
            )
            continue
        rv = rec.get("_record_version")
        if not isinstance(rv, int) or rv > EXPORT_RECORD_VERSION:
            errors.append(
                f"line {idx} (table {table!r}): _record_version {rv!r} > supported {EXPORT_RECORD_VERSION}"
            )
            continue
        if not rec.get("id"):
            errors.append(f"line {idx} (table {table!r}): missing required `id` field")
            continue
        if table in _DATA_TABLES:
            data_recs.append(rec)
        else:
            edge_recs.append(rec)
    if errors:
        raise ImportError_("validation failed:\n  " + "\n  ".join(errors))
    return data_recs, edge_recs


async def _assert_ledger_empty(adapter) -> None:
    """Pre-write gate. Skips _DELETE_BEFORE_IMPORT tables (auto-populate
    at connect; wiped in Phase B step 1). Raises ``ImportError_`` if
    any other table has rows."""
    for table in sorted(_DATA_TABLES | _EDGE_TABLES):
        if table in _DELETE_BEFORE_IMPORT:
            continue
        rows = await _gather_table_rows(adapter, table)
        if rows:
            raise ImportError_(
                f"target ledger non-empty (table {table!r} has {len(rows)} rows); "
                "run `bicameral-mcp reset` first to wipe before import"
            )


async def _delete_meta_tables(adapter) -> None:
    """Phase B step 1. DELETE FROM each _DELETE_BEFORE_IMPORT table."""
    for table in _DELETE_BEFORE_IMPORT:
        await adapter._client.execute(f"DELETE FROM {table}")


def _maybe_parse_datetime(value: Any) -> Any:
    """Parse ISO-format datetime strings back to datetime objects.

    SurrealDB datetime fields require datetime objects on write; the
    JSON round-trip flattens them to ISO strings via ``json.dumps(default=str)``.
    This helper detects that pattern (heuristic: 4-digit year prefix +
    contains 'T' or ' ' separator) and parses.
    """
    from datetime import datetime

    if not isinstance(value, str) or len(value) < 19:
        return value
    if not (value[:4].isdigit() and value[4] == "-"):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return value


def _rehydrate(content: dict) -> dict:
    """Walk content dict; rehydrate ISO datetime strings to datetime objects."""
    return {k: _maybe_parse_datetime(v) for k, v in content.items()}


def _strip_meta(record: dict, *extra: str) -> dict:
    """Return a shallow copy of `record` with metadata + `extra` removed,
    with ISO datetime strings rehydrated to datetime objects (required
    for SurrealDB option<datetime> fields)."""
    drop = {"_table", "_schema_version", "_record_version", *extra}
    return _rehydrate({k: v for k, v in record.items() if k not in drop})


async def _write_data_records(adapter, records: list[dict]) -> dict[str, int]:
    """Phase B step 2. CREATE <id> CONTENT $content per record."""
    counts: dict[str, int] = {}
    for rec in records:
        table = rec["_table"]
        rec_id = rec["id"]
        content = _strip_meta(rec, "id")
        await adapter._client.query(
            f"CREATE {rec_id} CONTENT $content",
            {"content": content},
        )
        counts[table] = counts.get(table, 0) + 1
    return counts


async def _write_edge_records(adapter, records: list[dict]) -> dict[str, int]:
    """Phase B step 3. RELATE <in>-><table>-><out> CONTENT $content per record."""
    counts: dict[str, int] = {}
    for rec in records:
        table = rec["_table"]
        in_id = rec.get("in")
        out_id = rec.get("out")
        if not in_id or not out_id:
            raise ImportError_(
                f"edge record on table {table!r} missing in/out fields: {rec.get('id')!r}"
            )
        content = _strip_meta(rec, "id", "in", "out")
        await adapter._client.query(
            f"RELATE {in_id}->{table}->{out_id} CONTENT $content",
            {"content": content},
        )
        counts[table] = counts.get(table, 0) + 1
    return counts


async def import_jsonl(adapter, lines: Iterable[str]) -> ImportSummary:
    """Two-phase import orchestrator: validate, then write data + edges."""
    data_recs, edge_recs = _validate_records(lines)
    await _assert_ledger_empty(adapter)
    await _delete_meta_tables(adapter)
    data_counts = await _write_data_records(adapter, data_recs)
    edge_counts = await _write_edge_records(adapter, edge_recs)
    return ImportSummary(
        data_records_written=data_counts,
        edge_records_written=edge_counts,
        total_records_written=sum(data_counts.values()) + sum(edge_counts.values()),
    )
