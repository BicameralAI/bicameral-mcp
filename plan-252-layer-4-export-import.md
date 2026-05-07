# Plan: #252 Layer 4 — portable JSON-Lines export/import as the migration vehicle (round 2)

**change_class**: feature

**doc_tier**: standard

**terms_introduced**:
- term: ledger-export
  home: cli/ledger_io.py
- term: ledger-import
  home: cli/ledger_io.py
- term: canonical export record
  home: cli/ledger_io.py
- term: export schema_version (`_schema_version`)
  home: cli/ledger_io.py
- term: export record_version (`_record_version`)
  home: cli/ledger_io.py
- term: ledger-export-output policy
  home: docs/policies/ledger-export.md

**boundaries**:
- limitations: Single-file JSON-Lines export/import; no incremental / partial / per-table flags in v1 (full ledger only — required for GDPR Art. 15 DSAR completeness). Operator runs `bicameral-mcp reset` separately if importing into a populated ledger; ledger-import always fail-fasts on non-empty target ledger (no `--force` shortcut in v1 — matches strategy brief's literal `export → reset → import` workflow). Two-pass import (data tables first, edge tables second) ensures referential integrity since RELATION-type edges need their `in`/`out` records to already exist. **Meta-table special case** (round-1 audit Path B): `bicameral_meta` and `schema_meta` are auto-populated at `adapter.connect()` time by Layer 2's `_emit_wire_format_sentinel` and `migrate()`'s `_set_schema_version` respectively, so the import logic **DELETEs both tables before writing source rows** to preserve the source's `at_first_write` provenance + recorded schema version (per the strategy brief's "migration vehicle" intent). Without this special case, the import would CREATE duplicate rows in both tables, breaking Layer 2's `SELECT ... LIMIT 1` invariant. Determinism: records sorted by `(table, created_at, id)` with `created_at` as primary sort key (neutralizes non-lexicographical ULID/time-based IDs) — supports diff-able backups + GitOps workflows. Strict-mode import: validates every record's required fields before any write; fail-fast at validation phase with operator-readable summary; rejects half-imported state by construction.
- non_goals: do not add `--redact` or `--include-content` flags in v1 — Layer 4's role is the GDPR Art. 15 DSAR + Art. 17 erasure vehicle, which requires a complete export. Operators wanting redacted output use `bicameral-mcp diagnose` (Layer 3) instead. Do not auto-upload anywhere — operator owns the dump file lifecycle end-to-end (privacy directive). Do not ship a SurrealQL replay-script alternative format — JSONL is the v1 contract. Do not perform mid-import schema migrations — operator runs `bicameral-mcp` against the destination ledger AFTER import to apply pending migrations to the imported data. Do not implement upsert-merge import semantics — clean wipe-first per strategy brief; reduces import-state-corruption surface by construction.
- exclusions: not modifying any DEFINE TABLE statements in `ledger/schema.py` (Layer 4 reads from the existing schema; no extension). Not modifying `ledger/queries.py` (export uses raw `client.query` for table-walk; doesn't reuse the typed query helpers because the export must be schema-walk-driven, not handler-shape-driven). Not modifying `audit_log.py` — no new event types; export/import emit existing `tool_invocation` events via `@server.call_tool()` wrapper inheritance. Not extending `bicameral-mcp reset` — operator runs reset separately per strategy brief workflow. Not adding a Python API for export/import outside of CLI invocation — v1 is CLI-only; programmatic embedding in handlers is YAGNI.

## Open Questions

All resolved during /qor-plan dialogue 2026-05-07:

- **Module location** (option a): `cli/ledger_io.py` (shared logic) + `cli/ledger_export_cli.py` + `cli/ledger_import_cli.py` (thin CLI shims). Matches `cli/_link_commit_runner.py` separation pattern.
- **Export format** (option a): JSON-Lines. Greppable, mid-file editable, streams cleanly for large ledgers. Per strategy brief.
- **Schema-version stamp** (option a — dual versioning): per-record `_table` + `_schema_version` (bicameral SQL schema, e.g., 16) + `_record_version` (export-format version, e.g., 1). Decouples format evolution from SQL schema bumps. Resilient to mid-export schema bumps.
- **Round-trip determinism** (option a refined): sort by `(table, created_at, id)` — `created_at` primary, `id` fallback. Neutralizes non-lexicographical ULID/time-based IDs; supports diff-able backups.
- **Edge handling** (option a): export RELATION-type edges as standalone records with `in` + `out` references. Two-pass import: data tables first, edge tables second. Preserves graph + edge-side fields (e.g., `binds_to.provenance`).
- **Field-level redaction** (option a): NO redaction — full ledger export. Operator's responsibility to redact pre-share. Layer 3 (`bicameral-mcp diagnose`) is the redacted surface; Layer 4 is the complete-export surface. Different tools for different jobs.
- **Import strategy** (option a, no `--force`): wipe-first via separate `bicameral-mcp reset` invocation by operator; ledger-import always fail-fasts on non-empty target. Per strategy brief literal workflow.
- **Error handling on import** (option a — strict + summary): two-phase. Phase A: validate every record's required fields against expected `_table` + `_schema_version` + `_record_version`; **accumulate ALL errors** before raising `ImportError_` with operator-readable summary listing every failure (not first-failure-only). Phase B: DELETE meta tables (`bicameral_meta`, `schema_meta`) → write data records → write edges. Phase A → Phase B is conditional on Phase A passing for every record.
- **Meta-table round-trip semantics** (round-1 audit Path B): the import special-cases `bicameral_meta` and `schema_meta` via a `_DELETE_BEFORE_IMPORT = frozenset({"bicameral_meta", "schema_meta"})` constant. Phase B's first action is `DELETE FROM <table>` for each table in the set; this removes the destination's auto-populated rows from `init_schema` + `migrate` + sentinel, freeing the slot for the source's rows. Layer 2's `SELECT ... LIMIT 1` invariant survives the round-trip because each table ends with exactly one row (the source's). Path A (skip these tables on import + drop the at_first_write claim) was rejected because the strategy brief frames Layer 4 as the "migration vehicle" — provenance must round-trip.

## Phase 1: shared `cli/ledger_io.py` — canonical record shape + export/import logic

### Affected Files

- `tests/test_ledger_io_canonical_record.py` — **new** functionality tests for `_canonical_record` shaping + version-stamp insertion + dataclass-shaped output ordering
- `tests/test_ledger_io_export.py` — **new** functionality tests for `export_jsonl(adapter)` async generator: emits records in sort order; data tables before edge tables; per-record metadata stamps present; round-trip determinism verified by hash comparison
- `tests/test_ledger_io_import.py` — **new** functionality tests for `import_jsonl(adapter, lines)`: fail-fast on non-empty ledger, fail-fast on invalid record (per-record validation summary), data-records-then-edges write order, post-import row counts match pre-export, round-trip equality verified
- `cli/ledger_io.py` — **new** module: `_DATA_TABLES` + `_EDGE_TABLES` frozensets; `EXPORT_RECORD_VERSION = 1` constant; `_canonical_record(table, row, schema_version)` shaper; `export_jsonl(adapter) -> AsyncIterator[str]` async generator; `import_jsonl(adapter, lines: Iterable[str]) -> ImportSummary` async function; `ImportSummary` dataclass + custom exceptions

### Changes

**`cli/ledger_io.py`** (new):

```python
"""Portable JSON-Lines export/import for the bicameral ledger (#252 Layer 4).

Closes #252 Layer 4 per
``docs/research-brief-252-privacy-preserving-ledger-remediation.md``.

The export is the GDPR Art. 15 (data-subject access) artifact + Art. 17
(right-to-erasure) escape hatch. Operators export → modify (e.g. delete
rows in the JSONL) → reset → import. The export format is JSON-Lines
with per-record version stamps for round-trip + future-format-evolution
safety.

Canonical record shape::

    {"_table": "decision", "_schema_version": 16, "_record_version": 1,
     "id": "decision:abc", "description": "...", "status": "ungrounded",
     ...}

Edges (RELATION-type tables) are exported as standalone records with
``in`` + ``out`` fields preserved::

    {"_table": "binds_to", "_schema_version": 16, "_record_version": 1,
     "id": "binds_to:xyz", "in": "decision:abc", "out": "code_region:def",
     "provenance": {...}, ...}

Privacy: no auto-upload, no redaction. Operator owns the dump file
lifecycle. Layer 3's ``bicameral-mcp diagnose`` is the redacted surface;
Layer 4 is the complete-export surface.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator, Iterable
from typing import Any

EXPORT_RECORD_VERSION = 1

# Data tables (DEFINE TABLE ... not RELATION). Hardcoded canonical list
# from `ledger/schema.py`'s grep at plan-text time. Adding a new table
# requires updating both the schema AND this constant; the parity is
# locked by `tests/test_ledger_io_canonical_record.py::test_data_and_edge_table_sets_cover_all_schema_define_table_statements`.
_DATA_TABLES: frozenset[str] = frozenset({
    "input_span", "decision", "symbol", "code_region", "vocab_cache",
    "ledger_sync", "source_cursor", "compliance_check", "graph_proposal",
    "code_subject", "subject_identity", "subject_version",
    "identity_supersedes",  # data-shaped despite edge semantics; no TYPE RELATION marker
    "schema_meta", "bicameral_meta",
})

# Edge tables (DEFINE TABLE ... TYPE RELATION).
_EDGE_TABLES: frozenset[str] = frozenset({
    "yields", "binds_to", "locates", "supersedes", "context_for",
    "depends_on", "has_identity", "has_version", "about",
})

_RESERVED_FIELD_NAMES = frozenset({"_table", "_schema_version", "_record_version"})

# Round-1 audit Path B: tables that the destination auto-populates at
# adapter.connect time (init_schema/migrate/sentinel), so the import
# DELETEs them before writing source rows. Preserves source-provenance
# round-trip semantics per Layer 2's drift-detection contract.
_DELETE_BEFORE_IMPORT: frozenset[str] = frozenset({"bicameral_meta", "schema_meta"})


class ExportError(Exception):
    """Raised on export-side failure (e.g., adapter not connected)."""


class ImportError_(Exception):
    """Raised on import-side validation failure with operator-readable summary."""


@dataclasses.dataclass(frozen=True)
class ImportSummary:
    """Returned by import_jsonl on success: counts written per table.

    Phase A (validation) failures raise ImportError_ before any write;
    callers receive ImportSummary only when Phase B (write) completed.
    """

    data_records_written: dict[str, int]  # table → row count
    edge_records_written: dict[str, int]
    total_records_written: int


def _canonical_record(table: str, row: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """Stamp the row with `_table` + `_schema_version` + `_record_version`.

    Returns a fresh dict with the metadata fields prepended (preserved by
    `json.dumps(sort_keys=True)`'s alphabetical ordering — `_table` etc.
    sort first because they start with `_`). Never mutates input.
    """
    record = {
        "_table": table,
        "_schema_version": schema_version,
        "_record_version": EXPORT_RECORD_VERSION,
    }
    for key, val in row.items():
        if key in _RESERVED_FIELD_NAMES:
            raise ExportError(
                f"row in table {table!r} has reserved field name {key!r}; "
                "schema-source field name conflicts with export metadata"
            )
        record[key] = val
    return record


def _record_sort_key(record: dict[str, Any]) -> tuple[str, str, str]:
    """Sort key: (table, created_at, id). created_at is primary so
    diff-stable backups don't churn on non-lexicographical ULID IDs."""
    return (
        record.get("_table", ""),
        str(record.get("created_at", "")),
        str(record.get("id", "")),
    )


async def _gather_table_rows(adapter, table: str) -> list[dict[str, Any]]:
    """Read all rows from `table` via raw client.query. Tolerates missing
    tables (returns []) so pre-Layer-2 ledgers don't break export."""
    try:
        rows = await adapter._client.query(f"SELECT * FROM {table}")
    except Exception:  # noqa: BLE001 — missing table is acceptable
        return []
    return rows if rows else []


async def export_jsonl(adapter) -> AsyncIterator[str]:
    """Yield JSON-Lines records for every row in every canonical table.

    Order: data tables (sorted by name), then edge tables (sorted by
    name). Within each table, records sorted by (table, created_at, id).
    Schema version read from `schema_meta.version` (Layer 2) or
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
```

The `import_jsonl` function decomposes into 4 private helpers + a ~15-LOC orchestrator (round-1 audit Razor advisory; per-function 40-LOC ceiling honored by construction):

```python
async def import_jsonl(
    adapter, lines: Iterable[str]
) -> ImportSummary:
    """Two-phase import orchestrator. Validates every record first;
    on validation success, deletes meta tables, writes data records,
    then edges. Returns ImportSummary with per-table counts."""
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


def _validate_records(
    lines: Iterable[str],
) -> tuple[list[dict], list[dict]]:
    """Phase A. Parse every line, validate `_table` ∈ _DATA_TABLES ∪ _EDGE_TABLES,
    validate `_schema_version` ≤ target SCHEMA_VERSION, validate `_record_version
    ≤ EXPORT_RECORD_VERSION`, validate required fields present.

    Accumulates ALL errors before raising — operator gets the full list once,
    not first-error-only. Returns (data_records, edge_records) on success.
    Raises ImportError_ with operator-readable multi-line summary on any failure.
    """
    # ~30 LOC: parse + per-record validate + collect errors + final raise


async def _assert_ledger_empty(adapter) -> None:
    """Pre-write gate. Counts rows across _DATA_TABLES ∪ _EDGE_TABLES;
    raises ImportError_ with operator instruction "run bicameral-mcp reset
    first" if any table has rows. Skips _DELETE_BEFORE_IMPORT tables from
    the empty check (those will be wiped in Phase B's first step)."""
    # ~15 LOC


async def _delete_meta_tables(adapter) -> None:
    """Phase B step 1. DELETEs each table in _DELETE_BEFORE_IMPORT so
    source-provenance can be written without conflicting with the
    adapter.connect-time auto-populated rows."""
    for table in _DELETE_BEFORE_IMPORT:
        await adapter._client.execute(f"DELETE FROM {table}")


async def _write_data_records(
    adapter, records: list[dict]
) -> dict[str, int]:
    """Phase B step 2. CREATE <id> CONTENT $content per record.
    Strips _table, _schema_version, _record_version from $content
    before writing. Returns {table: count} written."""
    # ~25 LOC


async def _write_edge_records(
    adapter, records: list[dict]
) -> dict[str, int]:
    """Phase B step 3. RELATE <in> -> <table> -> <out> CONTENT $content per
    record. Strips _table, _schema_version, _record_version, id, in, out
    from $content before writing (RELATE's in/out are positional, not body
    fields). Returns {table: count} written."""
    # ~25 LOC
```

### Unit Tests

- `tests/test_ledger_io_canonical_record.py` (**new**):
  - `test_canonical_record_stamps_table_schema_version_record_version` — invoke `_canonical_record("decision", {"id": "decision:abc", "description": "x"}, 16)`; assert returned dict has `_table == "decision"`, `_schema_version == 16`, `_record_version == 1`, plus the original keys.
  - `test_canonical_record_does_not_mutate_input` — pass dict; assert original dict unchanged after call.
  - `test_canonical_record_raises_on_reserved_field_name_collision` — invoke with `{"_table": "evil", ...}`; assert `ExportError` raised.
  - `test_data_and_edge_table_sets_cover_all_schema_define_table_statements` — read `ledger/schema.py`; grep all `DEFINE TABLE <name>` statements; assert every name appears in `_DATA_TABLES ∪ _EDGE_TABLES`. Locks future drift between schema and export-format.
  - `test_data_and_edge_table_sets_are_disjoint` — assert `_DATA_TABLES.isdisjoint(_EDGE_TABLES)`.
  - `test_record_sort_key_orders_by_table_then_created_at_then_id` — invoke `_record_sort_key` against fixtures; assert ordering follows the documented contract.
  - `test_export_record_version_is_pinned_int` — `EXPORT_RECORD_VERSION == 1`. Same shape as catalog-version pin tests.

- `tests/test_ledger_io_export.py` (**new**):
  - `test_export_jsonl_emits_no_records_for_empty_ledger` — invoke against fresh `memory://` adapter; assert async generator yields zero lines (only schema_meta/bicameral_meta rows from init are emitted; assert exactly the expected metadata records).
  - `test_export_jsonl_emits_data_tables_before_edge_tables` — populate ledger with one decision + one binds_to edge; collect generator output; assert decision record appears before binds_to record in the output stream.
  - `test_export_jsonl_records_sorted_by_table_created_at_id` — populate with 3 decisions in non-canonical insertion order (different created_at); assert exported records are sorted by (table, created_at, id).
  - `test_export_jsonl_each_record_has_metadata_stamps` — populate with one decision; parse first record; assert `_table`, `_schema_version`, `_record_version` all present + correct values.
  - `test_export_jsonl_round_trip_is_deterministic` — populate fixture ledger; export twice; assert byte-identical output. Locks the diff-stability contract.
  - `test_export_jsonl_handles_missing_bicameral_meta_table_gracefully` — invoke against pre-Layer-2-style ledger (drop the table manually); assert export completes without raising; missing table yields zero records for that table.
  - `test_export_jsonl_uses_schema_meta_version_when_present` — populate `schema_meta.version = 16`; export; assert every record carries `_schema_version: 16` (not the SCHEMA_VERSION constant if it differs).
  - `test_export_jsonl_falls_back_to_schema_version_constant_when_no_schema_meta` — drop schema_meta row; assert export still works using `SCHEMA_VERSION` constant fallback.
  - `test_export_jsonl_preserves_edge_in_out_fields` — populate one binds_to edge with provenance metadata; assert exported edge record has `in`, `out`, `provenance` all present.

- `tests/test_ledger_io_import.py` (**new**):
  - `test_import_jsonl_writes_data_records_before_edges` — provide JSONL with data records + edge records (in any order); after import, assert all data records exist + all edges resolve correctly.
  - `test_import_jsonl_round_trip_preserves_row_counts` — export → reset adapter → import → re-export; assert second export has same row count per table as first export.
  - `test_import_jsonl_round_trip_byte_equal_after_reimport` — export to fixture A; reset; import A; export to B; assert A == B byte-for-byte (locks deterministic round-trip).
  - `test_import_jsonl_fails_fast_on_non_empty_ledger` — populate ledger; invoke import; assert `ImportError_` raised with message instructing operator to run `bicameral-mcp reset` first; assert no records were written.
  - `test_import_jsonl_fails_fast_on_unknown_table` — provide JSONL with `{"_table": "evil_unknown", ...}`; assert `ImportError_` raised; no records written.
  - `test_import_jsonl_fails_fast_on_record_version_mismatch` — provide JSONL with `_record_version: 999`; assert `ImportError_` raised; no records written.
  - `test_import_jsonl_fails_fast_on_schema_version_mismatch_with_summary` — provide JSONL with `_schema_version: 99` (newer than target's SCHEMA_VERSION); assert `ImportError_` raised with both source + target schema versions in message.
  - `test_import_jsonl_returns_import_summary_with_per_table_counts` — happy path; assert `ImportSummary.data_records_written["decision"] == 3`, `edge_records_written["binds_to"] == 2`, `total_records_written == 5`.
  - `test_import_jsonl_validation_phase_collects_all_errors_before_aborting` — provide JSONL with 3 invalid records; assert `ImportError_` message lists all 3 (not just first); assert no records were written even though validation would have caught them all.
  - `test_import_jsonl_handles_edge_relation_via_relate_syntax` — provide one binds_to edge JSONL line; assert post-import `SELECT * FROM binds_to` returns the edge with correct in/out + side fields.
  - `test_import_jsonl_delete_before_import_preserves_source_at_first_write_provenance` — pre-populate destination's `bicameral_meta` with `at_first_write="dest-version"` (auto-populated by Layer 2's sentinel at connect time); source JSONL has `bicameral_meta` row with `at_first_write="source-version"`; invoke import; post-import assert `SELECT surrealdb_client_version_at_first_write FROM bicameral_meta` returns `"source-version"` (NOT `"dest-version"`). Locks the round-1 audit Path B contract.
  - `test_import_jsonl_delete_before_import_resolves_schema_meta_to_source_version` — same shape: destination's `schema_meta.version` populated to current `SCHEMA_VERSION` via migrate; source JSONL has `schema_meta.version=15` (older); post-import assert `_get_schema_version` returns `15`. Locks the source-version-wins semantic for the migration-vehicle use case.
  - `test_import_jsonl_delete_before_import_yields_exactly_one_row_per_meta_table` — post-import assert `SELECT count() AS n FROM bicameral_meta GROUP ALL` returns `[{"n": 1}]` and same for `schema_meta`. Locks the no-duplicate-rows invariant that broke without Path B.

## Phase 2: CLI shims + server.py subparser registration

### Affected Files

- `tests/test_ledger_export_cli.py` — **new** functionality tests for `cli.ledger_export_cli.main()` end-to-end via direct invocation (stdout capture)
- `tests/test_ledger_import_cli.py` — **new** functionality tests for `cli.ledger_import_cli.main()` end-to-end via stdin pipe (file path argument)
- `cli/ledger_export_cli.py` — **new** thin shim: parses CLI args, opens adapter, streams `export_jsonl(adapter)` to stdout, returns 0
- `cli/ledger_import_cli.py` — **new** thin shim: parses `--from-file <path>` arg, opens adapter, reads file lines, calls `import_jsonl(adapter, lines)`, prints summary, returns 0/1
- `server.py` — register `ledger-export` + `ledger-import` subparsers + dispatch arms

### Changes

**`cli/ledger_export_cli.py`** (new, ~30 LOC):

```python
"""CLI entrypoint for `bicameral-mcp ledger-export` (#252 Layer 4)."""

from __future__ import annotations

import asyncio
import sys


def main() -> int:
    """Stream JSON-Lines export to stdout. Returns 0 on success, 1 on
    adapter-connect failure."""
    from cli.ledger_io import export_jsonl
    from ledger.adapter import SurrealDBLedgerAdapter

    async def _run() -> int:
        adapter = SurrealDBLedgerAdapter()
        await adapter.connect()
        async for line in export_jsonl(adapter):
            sys.stdout.write(line + "\n")
        return 0

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — operator needs failure context
        sys.stderr.write(f"ledger-export: adapter connect or query failed: {exc}\n")
        return 1
```

**`cli/ledger_import_cli.py`** (new, ~50 LOC):

```python
"""CLI entrypoint for `bicameral-mcp ledger-import` (#252 Layer 4)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def main(from_file: str | None = None) -> int:
    """Read JSONL from stdin or `--from-file <path>` and import into the
    ledger. Returns 0 on success with summary printed to stdout, 1 on
    validation/import failure with summary printed to stderr."""
    from cli.ledger_io import ImportError_, import_jsonl
    from ledger.adapter import SurrealDBLedgerAdapter

    if from_file:
        try:
            lines = Path(from_file).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            sys.stderr.write(f"ledger-import: cannot read {from_file}: {exc}\n")
            return 1
    else:
        lines = sys.stdin.read().splitlines()

    async def _run() -> int:
        adapter = SurrealDBLedgerAdapter()
        await adapter.connect()
        try:
            summary = await import_jsonl(adapter, lines)
        except ImportError_ as exc:
            sys.stderr.write(f"ledger-import: validation failed:\n{exc}\n")
            return 1
        sys.stdout.write(
            f"ledger-import: wrote {summary.total_records_written} records "
            f"({sum(summary.data_records_written.values())} data + "
            f"{sum(summary.edge_records_written.values())} edges)\n"
        )
        return 0

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"ledger-import: adapter connect failed: {exc}\n")
        return 1
```

**`server.py`** (extensions):

```python
# In _register_subparsers, add after the existing diagnose subparser:
subparsers.add_parser(
    "ledger-export",
    help="export the full ledger as JSON-Lines to stdout (#252 Layer 4)",
)
import_parser = subparsers.add_parser(
    "ledger-import",
    help="import a JSON-Lines ledger dump (#252 Layer 4)",
)
import_parser.add_argument(
    "--from-file",
    default=None,
    metavar="PATH",
    help="read JSONL from file instead of stdin",
)

# In _dispatch, add after the diagnose arm:
if args.command == "ledger-export":
    from cli.ledger_export_cli import main as export_main

    return export_main()
if args.command == "ledger-import":
    from cli.ledger_import_cli import main as import_main

    return import_main(getattr(args, "from_file", None))
```

### Unit Tests

- `tests/test_ledger_export_cli.py` (**new**):
  - `test_export_cli_returns_zero_on_fresh_memory_ledger` — set `SURREAL_URL=memory://`; invoke `main()`; assert exit 0.
  - `test_export_cli_emits_jsonl_to_stdout` — same setup with one decision pre-populated; capture stdout; assert each line parses as JSON with `_table` field; assert decision record present.
  - `test_export_cli_returns_one_on_adapter_failure` — monkeypatch adapter.connect to raise; assert exit 1; assert error on stderr.

- `tests/test_ledger_import_cli.py` (**new**):
  - `test_import_cli_reads_from_file_argument` — write fixture JSONL to tmp_path; invoke `main(from_file=str(tmp_path / "fixture.jsonl"))`; assert exit 0; assert summary printed.
  - `test_import_cli_reads_from_stdin_when_no_file` — pipe JSONL via `monkeypatch.setattr("sys.stdin", io.StringIO(jsonl_string))`; invoke `main()`; assert exit 0.
  - `test_import_cli_returns_one_on_validation_failure` — provide invalid JSONL (unknown table); assert exit 1; assert validation summary on stderr.
  - `test_import_cli_returns_one_on_unreadable_file` — `from_file=str(tmp_path / "missing.jsonl")`; assert exit 1; assert OS error message.
  - `test_import_cli_round_trip_export_then_reset_then_import_preserves_state` — populate ledger with 5 decisions + 3 edges → export to tmp file → reset adapter (or use fresh memory://) → import from file → assert post-import row counts match pre-export. End-to-end CLI-level roundtrip lock.

## Phase 3: operator policy doc + content-contract tests + README row

### Affected Files

- `tests/test_compliance_policy_docs.py` — extend with `test_ledger_export_policy_doc_lists_canonical_record_fields` + `test_ledger_export_policy_doc_documents_two_pass_import_and_gdpr_use_cases`
- `docs/policies/ledger-export.md` — **new** operator-readable policy: canonical record format spec; metadata-stamp semantics (`_table` + `_schema_version` + `_record_version`); two-pass import (data → edges) rationale; GDPR Art. 15 (DSAR) + Art. 17 (right-to-erasure) workflow recipes; round-trip determinism contract; size estimate guidance; error-mode catalog
- `README.md` — extend "Compliance posture" section: bump from 6 → 7 policy files; add `ledger-export.md` row

### Changes

**`docs/policies/ledger-export.md`** (new):

```markdown
# `bicameral-mcp ledger-export` / `ledger-import` policy

Closes **#252 Layer 4** of the privacy-preserving ledger-remediation strategy. Provides the portable JSON-Lines export/import vehicle that doubles as:

- The **GDPR Art. 15 DSAR** (data-subject access) artifact when an operator needs to provide a complete data dump
- The **GDPR Art. 17 right-to-erasure** escape hatch (operator exports → edits the JSONL → resets → reimports)
- The **migration vehicle** when surrealdb-py wire-format bumps require a clean re-canonicalization

## Canonical record shape

Every line in the export file is a JSON object with:

| Field | Type | Purpose |
|---|---|---|
| `_table` | str | Originating table name (e.g., `"decision"`, `"binds_to"`) |
| `_schema_version` | int | bicameral SQL schema version at export time (e.g., `16`) |
| `_record_version` | int | Export-format version (currently `1`) |
| `id` | str | SurrealDB record ID (e.g., `"decision:abc..."`) |
| `created_at` | str | ISO-formatted timestamp (when present in source row) |
| ... source fields | various | Verbatim from the source row |
| `in` / `out` (edges only) | str | Edge endpoint record IDs (RELATION-type tables) |

## Workflow recipes

### Backup (operator-controlled)

```bash
bicameral-mcp ledger-export > ~/bicameral-backup-$(date +%Y%m%d).jsonl
```

### GDPR Art. 17 right-to-erasure

```bash
bicameral-mcp ledger-export > /tmp/erasure-staging.jsonl
# Edit /tmp/erasure-staging.jsonl: remove records matching the erasure request
bicameral-mcp reset
bicameral-mcp ledger-import --from-file /tmp/erasure-staging.jsonl
```

### GDPR Art. 15 DSAR (data-subject access)

```bash
bicameral-mcp ledger-export > /tmp/dsar-response.jsonl
# Provide /tmp/dsar-response.jsonl to the data subject; redact non-subject records first
```

### Migration vehicle (post-surrealdb-bump)

```bash
bicameral-mcp ledger-export > /tmp/migration.jsonl
pip install --upgrade surrealdb==<new-version>  # bump pin in pyproject.toml + reinstall
bicameral-mcp reset
bicameral-mcp ledger-import --from-file /tmp/migration.jsonl
```

## Two-pass import rationale

RELATION-type edges in SurrealDB require their `in` and `out` records to already exist before they can be RELATEd. The import logic enforces this via two passes:

1. **Pass A — data records**: write every record from `_DATA_TABLES` first via `CREATE <id> CONTENT $content`.
2. **Pass B — edge records**: write every record from `_EDGE_TABLES` second via `RELATE $in -> <table> -> $out CONTENT $content`.

Mid-pass failures abort the import; the validation phase ensures every record passes the format check before any write occurs, so the only mid-import failure mode is filesystem / SurrealDB transient errors.

## Round-trip determinism

Records are sorted by `(table, created_at, id)` with `created_at` as the primary sort key. This neutralizes non-lexicographical ULID/time-based record IDs and supports diff-able backups + GitOps workflows. Re-exporting an unchanged ledger produces byte-identical output (locked by `tests/test_ledger_io_export.py::test_export_jsonl_round_trip_is_deterministic`).

### Meta-table special case

`bicameral_meta` (Layer 2's wire-format sentinel) and `schema_meta` (the bicameral SQL schema version) are auto-populated by `adapter.connect()` time — `init_schema` + `migrate` + Layer 2's `_emit_wire_format_sentinel` write destination-side rows before the import logic runs. To preserve source-provenance round-trip (especially `surrealdb_client_version_at_first_write`), the import logic **deletes both tables** before writing source rows. Mechanism:

1. Operator runs `bicameral-mcp reset` (deletes `~/.bicameral/ledger.db` entirely)
2. Operator runs `bicameral-mcp ledger-import --from-file <path>`:
   - `adapter.connect()` runs init_schema + migrate + sentinel → both meta tables have one destination-side row each
   - Phase A validates every JSONL record
   - Phase B step 1: `DELETE FROM bicameral_meta` + `DELETE FROM schema_meta` (clears the destination rows)
   - Phase B step 2: writes data records from JSONL — the source's `bicameral_meta` row with its `at_first_write` provenance lands here
   - Phase B step 3: writes edge records via RELATE

End state: each meta table has exactly the source's row. Layer 2's drift-detection contract works on the imported ledger as if the source-binary had populated it directly.

## Privacy posture

- **No auto-upload**: the dump file is written to a path of the operator's choice (stdout redirect or `--from-file <path>`); never piped through any service.
- **No redaction**: full ledger export is required for GDPR Art. 15 DSAR completeness. Operators wanting redacted output use `bicameral-mcp diagnose` (Layer 3) instead.
- **Operator owns lifecycle**: the dump file's retention, distribution, and disposal are operator decisions; bicameral-mcp does not retain a copy.

## Error modes

| Error | Cause | Operator action |
|---|---|---|
| `ledger-import: validation failed: <records>` | One or more records failed the canonical-shape validation | Fix the JSONL file (or re-export from source) and retry |
| `ledger-import: ledger non-empty; run bicameral-mcp reset first` | Target ledger has records | Run `bicameral-mcp reset` to wipe, then retry import |
| `ledger-import: schema_version <X> from source > target SCHEMA_VERSION <Y>` | Source export was generated by a newer binary | Upgrade bicameral-mcp to a binary that supports schema X, then retry |
| `ledger-export: adapter connect or query failed` | Local SurrealKV at `~/.bicameral/ledger.db` is unreachable | Check filesystem permissions; consider `bicameral-mcp diagnose` for full context |

## References

- `cli/ledger_io.py` — shared canonical-record + export/import logic
- `cli/ledger_export_cli.py` / `cli/ledger_import_cli.py` — thin CLI shims
- `tests/test_ledger_io_*.py` — functional test suite (~30 tests)
- `docs/research-brief-252-privacy-preserving-ledger-remediation.md` — Layer 4 strategy
- `docs/policies/diagnose-output.md` — sister surface (#252 Layer 3); Layer 3 is the redacted operator-bug-report tool, Layer 4 is the complete-ledger DSAR/erasure tool
```

**`README.md`** (extension): bump 6 → 7 policy files; add `ledger-export.md` row.

### Unit Tests

- `tests/test_compliance_policy_docs.py` (extension):
  - `test_ledger_export_policy_doc_lists_canonical_record_fields` — read `docs/policies/ledger-export.md`; assert each canonical field name (`_table`, `_schema_version`, `_record_version`, `id`, `created_at`, `in`, `out`) appears in the doc's record-shape table.
  - `test_ledger_export_policy_doc_documents_two_pass_import_and_gdpr_use_cases` — assert the doc enumerates: "Pass A — data records", "Pass B — edge records", "Art. 15", "Art. 17", "right-to-erasure", "migration vehicle". Locks the workflow-recipe + use-case catalog against drift.

## CI Commands

- `pytest tests/test_ledger_io_canonical_record.py tests/test_ledger_io_export.py tests/test_ledger_io_import.py -v` — Phase 1 (canonical-record + export + import logic).
- `pytest tests/test_ledger_export_cli.py tests/test_ledger_import_cli.py -v` — Phase 2 (CLI shims).
- `pytest tests/test_compliance_policy_docs.py -v` — Phase 3 (policy-doc content contract).
- `pytest tests/test_ledger_io_*.py tests/test_ledger_*_cli.py tests/test_compliance_policy_docs.py tests/test_ledger_bicameral_meta_*.py tests/test_diagnose_*.py -q` — full Layer-4-and-sister regression.
- `pytest tests/ -q` — broader regression baseline (1000+ tests).
- `ruff check cli/ledger_io.py cli/ledger_export_cli.py cli/ledger_import_cli.py server.py tests/test_ledger_io_*.py tests/test_ledger_*_cli.py` — lint clean.
- `ruff format --check cli/ledger_io.py cli/ledger_export_cli.py cli/ledger_import_cli.py server.py tests/test_ledger_io_*.py tests/test_ledger_*_cli.py` — format clean.

## Implementer notes

- **Module split mandate** (round-1 audit advisory): `cli/ledger_io.py` plan-estimated at ~280 LOC (over the 250-LOC file Razor); implementer MUST split at write-time into `cli/ledger_io.py` (constants + frozensets + `Diagnosis`-shape dataclass + custom exceptions + `_canonical_record` + `_record_sort_key` ≈ 150 LOC) + `cli/_ledger_io_engine.py` (`_gather_table_rows` + `export_jsonl` + `import_jsonl` orchestrator + `_validate_records` + `_assert_ledger_empty` + `_delete_meta_tables` + `_write_data_records` + `_write_edge_records` ≈ 130 LOC). Same shape as Layer 3's `cli/_diagnose_gather.py` precedent. Both files MUST stay under 250 LOC.
- **`import_jsonl` decomposition mandate** (round-1 audit advisory): the orchestrator MUST stay under the 40-LOC function ceiling. Implementation MUST extract the 5 private helpers documented in the Phase 1 Changes section: `_validate_records`, `_assert_ledger_empty`, `_delete_meta_tables`, `_write_data_records`, `_write_edge_records`. The orchestrator becomes a ~15-LOC sequence of helper calls. Locked at substantiate time by the per-function Razor check.
- **Two-pass import write-order**: data records MUST land before edges. Implementation: collect all data lines first into `data_buffer`, all edge lines into `edge_buffer`; validate both; write data_buffer; write edge_buffer. Order within each buffer follows the input JSONL line order (which is sorted by the export logic, so the order survives the round-trip).
- **`SELECT * FROM <table>` for export**: SurrealDB v2 embedded does not paginate `SELECT *` against large tables; large ledgers (>100 MiB) may exhaust memory. Documented in `docs/policies/ledger-export.md` as v1 limitation; future Layer 4 enhancement (paginated streaming export) deferred to v2 if operator telemetry shows demand.
- **`identity_supersedes` is data-shaped despite edge semantics** — modeled as a regular table with manual `in`/`out` fields, not a SurrealDB-native RELATION. Stays in `_DATA_TABLES`. Documented in the constant's docstring.
- **`schema_meta` row count + round-trip**: `_set_schema_version` does DELETE+CREATE on every migration step, so `schema_meta` has exactly one row in steady state. The export captures that one row; the import's `_delete_meta_tables` clears the destination's auto-populated row, and the data-records pass writes the source's row. Subsequent migrations on the destination ledger re-DELETE+CREATE the row anyway, so the source's `version` field is the operative state until any future migrate() call advances it.
- **`bicameral_meta` round-trip via DELETE-before-CREATE** (round-1 audit Path B): the row's `surrealdb_client_version_at_first_write` field is preserved across export/import via the `_DELETE_BEFORE_IMPORT` special case. Mechanism: `adapter.connect()` runs `init_schema` + `migrate` + sentinel which populate `bicameral_meta` with destination-side `at_first_write`. Phase B's first action is `_delete_meta_tables(adapter)` which `DELETE FROM bicameral_meta` and `DELETE FROM schema_meta` before any data-record writes. Then the data-records pass writes the source's rows from the JSONL — the destination ends with exactly one row per meta table, carrying the source's provenance. Layer 2's `SELECT ... LIMIT 1` invariant survives. The same mechanism preserves `schema_meta.version` from the source.
- **Re-canonicalization migration use case**: the strategy brief frames Layer 4 as "migration happens by re-canonicalization." The export reads the source ledger via the adapter (which uses the source's surrealdb-py); the JSONL is wire-format-independent (pure JSON); the import writes via the destination's surrealdb-py. So the export/import roundtrip naturally handles surrealdb-py wire-format bumps without needing intermediate translation.
- **Validation Phase A error accumulation**: the validation pass MUST collect all errors before raising — the operator receives the full list once, not the first error per re-run. Locked by `test_import_jsonl_validation_phase_collects_all_errors_before_aborting`.
- **Edge `in` and `out` fields**: SurrealDB's RELATE syntax is `RELATE <in> -> table -> <out> CONTENT $body`. The body MUST NOT include `in` or `out` (they're positional in the RELATE statement). Implementation: strip `in`, `out`, `id`, and the `_*` metadata fields before passing the rest as `$content`.
