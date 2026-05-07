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

Mid-pass failures abort the import; the validation phase ensures every record passes the format check before any write occurs.

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
| `ledger-import: target ledger non-empty (table 'X' has N rows); run \`bicameral-mcp reset\` first` | Target ledger has records | Run `bicameral-mcp reset` to wipe, then retry import |
| `ledger-import: line N: _schema_version <X> > target SCHEMA_VERSION <Y>` | Source export was generated by a newer binary | Upgrade bicameral-mcp to a binary that supports schema X, then retry |
| `ledger-export: adapter connect or query failed` | Local SurrealKV at `~/.bicameral/ledger.db` is unreachable | Check filesystem permissions; consider `bicameral-mcp diagnose` for full context |

## References

- `cli/ledger_io.py` — constants + canonical-record shape (≤150 LOC)
- `cli/_ledger_io_engine.py` — async export/import + 5 private helpers
- `cli/ledger_export_cli.py` / `cli/ledger_import_cli.py` — thin CLI shims
- `tests/test_ledger_io_*.py` — functional test suite (~30 tests)
- `docs/research-brief-252-privacy-preserving-ledger-remediation.md` — Layer 4 strategy
- `docs/policies/diagnose-output.md` — sister surface (#252 Layer 3); Layer 3 is the redacted operator-bug-report tool, Layer 4 is the complete-ledger DSAR/erasure tool
