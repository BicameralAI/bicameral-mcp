# Plan: #252 Layer 2 — schema-revision sentinel for surrealdb-py wire-format awareness

**change_class**: feature

**doc_tier**: standard

**terms_introduced**:
- term: wire-format sentinel
  home: ledger/schema.py
- term: surrealdb-py client version (recorded)
  home: ledger/schema.py
- term: ledger_schema_verified (audit-log event)
  home: audit_log.py
- term: ledger_version_drift (audit-log event)
  home: audit_log.py

**boundaries**:
- limitations: WARN-only posture per operator directive ("gating is observability, not obstruction"). The sentinel records `surrealdb_client_version_at_first_write` + `surrealdb_client_version_at_last_write` + `last_write_at` and emits an audit-log event on every server boot. Drift between the recorded "last write" version and the running version emits `ledger_version_drift` at warn level — does NOT fail-fast. The actual breaking case ("Invalid revision N for type Value") already fail-fasts at the SurrealDB deserialization layer; Layer 2's value is identifying the cause via structured emission, not adding a second gate. Hard gating is reserved for Layer 5 (opt-in auto-migrate) where it pairs with a migration path.
- non_goals: do not add a `--strict` mode in v1 that converts the WARN to a fail-fast — operator directive forbids that escalation in Layer 2 specifically; it belongs in Layer 5 where the operator opts into the gating contract. Do not migrate the existing `schema_meta.version` (bicameral SQL schema) — that's a separate dimension already managed by the existing migration framework. Do not introduce a "minimum surrealdb-py version" enforcement; the pin in pyproject.toml (#252 Layer 1) handles the enforcement at install time, and runtime should not double-gate.
- exclusions: not modifying any `_migrate_vN_to_vM` function; the new sentinel field is additive and lives alongside the existing `version` field on the same `schema_meta` table. Not modifying `audit_log.py`'s public `emit()` or `JsonFormatter` — only adding two new `AuditEventType` enum values + their level mapping. Not extending `tests/test_compliance_policy_docs.py` here (Layer 4 covers the export-format policy doc; Layer 2 is internals-only). Not modifying the operator-facing `docs/policies/audit-log.md` event-taxonomy table here either — Layer 2's two new events get folded into the table when Layer 4 ships its doc updates (defer to avoid two-PR doc churn for the same surface).

## Open Questions

All resolved during /qor-plan dialogue 2026-05-07:

- **Sentinel location**: extend the existing `schema_meta` table with a `wire_format` field (option c — both dimensions tracked). Justification: reuses the existing migration framework + persistence layer; the bicameral SQL schema and surrealdb-py wire format are conceptually distinct but live on the same meta surface.
- **Sentinel content shape**: option (b) — `surrealdb_client_version_at_first_write` (immutable after first set) + `surrealdb_client_version_at_last_write` (updated on every server boot) + `last_write_at` (datetime). Minimum useful for diagnosing drift; full audit trail belongs in `audit_log` (already the operator-facing surface).
- **Mismatch response**: option (b) — WARN + audit-log emit; proceed. Per operator directive ("gating is observability, not obstruction"). Fail-fast already happens at the SurrealDB deserialization layer when wire-format is incompatible; Layer 2 names the cause without adding a second gate.
- **When does the check run**: option (b) — once at server boot via `init_schema()`. `init_schema()` is the natural insertion point because it already runs at startup and already touches the `schema_meta` table for the bicameral schema-version write/check.
- **Audit-log event types**: option (a) — two new `AuditEventType` enum values: `LEDGER_SCHEMA_VERIFIED` (info-level, fires on match) + `LEDGER_VERSION_DRIFT` (warn-level, fires on mismatch). Distinct event types let operators filter cleanly via `BICAMERAL_AUDIT_LOG_LEVEL`.

## Phase 1: extend `schema_meta` with wire-format fields + write-on-init helper

### Affected Files

- `tests/test_ledger_schema_meta_wire_format.py` — **new** functionality tests for the extended `schema_meta` row write/read semantics
- `tests/test_ledger_schema_meta_migration.py` — **new** functionality tests for the v15→v16 migration that adds the `wire_format` field to existing ledgers
- `ledger/schema.py` — add three new `schema_meta` fields (`surrealdb_client_version_at_first_write`, `surrealdb_client_version_at_last_write`, `last_write_at`); add `_migrate_v15_to_v16` that populates the new fields on existing ledgers; bump `SCHEMA_VERSION` to 16; add helper `_write_wire_format_sentinel(client, surrealdb_version)` invoked from `init_schema()` after migrations complete

### Changes

**`ledger/schema.py`** — bump `SCHEMA_VERSION` and extend the `_META` definitions:

```python
SCHEMA_VERSION = 16

SCHEMA_COMPATIBILITY: dict[int, str] = {
    # ... existing entries unchanged ...
    16: "0.13.x",  # #252 Layer 2 — wire-format sentinel; placeholder, release-eng pins final value at PR merge
}

_META = [
    "DEFINE TABLE schema_meta SCHEMAFULL",
    "DEFINE FIELD version     ON schema_meta TYPE int",
    "DEFINE FIELD migrated_at ON schema_meta TYPE datetime DEFAULT time::now()",
    # #252 Layer 2 — wire-format sentinel. Tracks the surrealdb-py client
    # version that wrote/touched this ledger so a future runtime can detect
    # client-version drift before the SurrealDB deserializer raises
    # "Invalid revision N for type Value".
    "DEFINE FIELD surrealdb_client_version_at_first_write ON schema_meta TYPE option<string> DEFAULT NONE",
    "DEFINE FIELD surrealdb_client_version_at_last_write  ON schema_meta TYPE option<string> DEFAULT NONE",
    "DEFINE FIELD last_write_at                            ON schema_meta TYPE option<datetime> DEFAULT NONE",
]
```

**`ledger/schema.py`** — new `_migrate_v15_to_v16`:

```python
async def _migrate_v15_to_v16(client: LedgerClient) -> None:
    """#252 Layer 2: extend schema_meta with wire-format sentinel fields.

    No data migration required — the new fields are option<...> with
    NONE default. The first-write value is populated by
    _write_wire_format_sentinel() at the end of init_schema; existing
    ledgers will record the upgrading-binary's version as the
    "first write" because we have no archaeological record of which
    version originally wrote them. Documented in the field docstring.
    """
    # No-op migration body; the schema_meta DEFINE FIELDs above are
    # already applied via init_schema's OVERWRITE-pass. This entry
    # exists so the migration registry sees v15 -> v16 as a known
    # forward path (avoids SchemaVersionTooNew on existing v15 ledgers).
    return
```

Add to the migration registry:

```python
_MIGRATIONS: dict[int, Callable[[LedgerClient], Awaitable[None]]] = {
    # ... existing entries 5..15 unchanged ...
    16: _migrate_v15_to_v16,
}
```

**`ledger/schema.py`** — new helper invoked from `init_schema()` after migrations:

```python
async def _write_wire_format_sentinel(client: LedgerClient) -> tuple[str | None, str | None, str]:
    """Read the recorded surrealdb-py version from schema_meta and update the
    last-write fields with the running version.

    Returns a tuple ``(recorded_version, running_version, status)`` where
    ``status`` is one of ``"first-write"`` (no prior recorded version),
    ``"match"`` (recorded == running), or ``"drift"`` (recorded != running).

    Side effects: updates ``surrealdb_client_version_at_last_write`` and
    ``last_write_at`` on the singleton ``schema_meta`` row. Sets
    ``surrealdb_client_version_at_first_write`` only when it was previously
    NONE (immutable after first set).

    Caller (init_schema) is responsible for emitting the audit-log event
    based on the returned status; this helper does not import audit_log to
    keep the ledger module's dependency surface tight.
    """
    import importlib.metadata

    try:
        running = importlib.metadata.version("surrealdb")
    except importlib.metadata.PackageNotFoundError:
        running = "unknown"

    rows = await client.query("SELECT * FROM schema_meta LIMIT 1")
    if not rows:
        # Brand-new ledger; init_schema's version-write step will create
        # the schema_meta row. Re-query post-write.
        return None, running, "first-write"

    row = rows[0]
    recorded = row.get("surrealdb_client_version_at_last_write")
    first = row.get("surrealdb_client_version_at_first_write")

    update_sql = (
        "UPDATE schema_meta SET "
        "surrealdb_client_version_at_last_write = $running, "
        "last_write_at = time::now()"
    )
    bindings: dict[str, str] = {"running": running}
    if first is None:
        update_sql += ", surrealdb_client_version_at_first_write = $running"
    await client.query(update_sql, bindings)

    if recorded is None:
        return None, running, "first-write"
    if recorded == running:
        return recorded, running, "match"
    return recorded, running, "drift"
```

**`ledger/schema.py`** — call the helper from `init_schema()` and emit audit-log event based on status:

```python
async def init_schema(client: LedgerClient) -> None:
    # ... existing body unchanged through _ANALYZERS / _TABLES / _META / migrations ...

    # #252 Layer 2 — wire-format sentinel write + observability emit.
    recorded, running, status = await _write_wire_format_sentinel(client)
    try:
        from audit_log import AuditEventType, emit as audit_emit

        if status == "drift":
            audit_emit(
                AuditEventType.LEDGER_VERSION_DRIFT,
                surrealdb_client_version_recorded=recorded,
                surrealdb_client_version_running=running,
                bicameral_schema_version=SCHEMA_VERSION,
            )
        else:
            audit_emit(
                AuditEventType.LEDGER_SCHEMA_VERIFIED,
                surrealdb_client_version_running=running,
                bicameral_schema_version=SCHEMA_VERSION,
                status=status,  # "first-write" | "match"
            )
    except Exception:  # noqa: BLE001 — observability MUST NOT break boot
        pass
```

### Unit Tests

- `tests/test_ledger_schema_meta_wire_format.py` (**new**):
  - `test_write_wire_format_sentinel_returns_first_write_on_empty_ledger` — invoke `_write_wire_format_sentinel` against an in-memory ledger with no `schema_meta` row; assert returned status is `"first-write"`, `running` is the importlib.metadata-resolved surrealdb version.
  - `test_write_wire_format_sentinel_records_running_version_on_first_call` — invoke against a ledger with `schema_meta` row but NONE wire-format fields; query the row post-call; assert `surrealdb_client_version_at_first_write == surrealdb_client_version_at_last_write == running` and `last_write_at` is non-NONE.
  - `test_write_wire_format_sentinel_preserves_first_write_on_subsequent_call` — invoke twice with monkeypatched `importlib.metadata.version` returning different values per call; assert `surrealdb_client_version_at_first_write` retains the first call's value while `surrealdb_client_version_at_last_write` updates to the second call's value.
  - `test_write_wire_format_sentinel_returns_match_when_versions_equal` — invoke twice with the same monkeypatched version; second call's returned tuple has status `"match"`.
  - `test_write_wire_format_sentinel_returns_drift_when_versions_differ` — invoke once at v2.0.0, then once at v2.1.0 (monkeypatched); second call's returned tuple has status `"drift"` with `recorded == "2.0.0"` and `running == "2.1.0"`.
  - `test_write_wire_format_sentinel_running_unknown_when_package_missing` — monkeypatch `importlib.metadata.version` to raise `PackageNotFoundError`; assert returned `running == "unknown"` and the helper does not raise.
  - `test_init_schema_emits_ledger_schema_verified_on_first_write` — patch `audit_log.emit` with a capture stub; run `init_schema` against a fresh in-memory ledger; assert one `LEDGER_SCHEMA_VERIFIED` emit with `status="first-write"`.
  - `test_init_schema_emits_ledger_version_drift_on_recorded_mismatch` — pre-populate `schema_meta` with `surrealdb_client_version_at_last_write = "2.0.0"`, monkeypatch the running version to `"2.1.0"`, run `init_schema`; assert one `LEDGER_VERSION_DRIFT` emit with `recorded="2.0.0"` and `running="2.1.0"`.
  - `test_init_schema_audit_log_emit_failure_does_not_break_boot` — monkeypatch `audit_log.emit` to raise; run `init_schema` against a fresh ledger; assert `init_schema` returns normally and the schema_meta row was written correctly.

- `tests/test_ledger_schema_meta_migration.py` (**new**):
  - `test_migrate_v15_to_v16_is_no_op_for_existing_v15_ledger` — pre-populate `schema_meta` at version=15 with no wire-format fields; run `migrate(client)`; assert version becomes 16 and the wire-format fields are still NONE (the `_write_wire_format_sentinel` helper writes them, NOT the migration body).
  - `test_migration_registry_includes_v15_to_v16` — `_MIGRATIONS[16] is _migrate_v15_to_v16` (catalog membership lock; same shape as canary catalog version pin).

## Phase 2: AuditEventType enum extension + level mapping

### Affected Files

- `tests/test_audit_log_ledger_event_types.py` — **new** functionality tests for the two new enum values + their level-table entries
- `audit_log.py` — add two new `AuditEventType` enum values (`LEDGER_SCHEMA_VERIFIED`, `LEDGER_VERSION_DRIFT`) + their entries in `_LEVEL_BY_EVENT`

### Changes

**`audit_log.py`** — extend the closed enum + level table:

```python
class AuditEventType(enum.StrEnum):
    TOOL_INVOCATION = "tool_invocation"
    SERVER_START = "server_start"
    SERVER_SHUTDOWN = "server_shutdown"
    CONFIG_LOAD = "config_load"
    INGEST_REFUSAL = "ingest_refusal"
    PREFLIGHT_BYPASS = "preflight_bypass"
    GATE_FIRED = "gate_fired"
    ERROR = "error"
    # #252 Layer 2 — wire-format sentinel observability
    LEDGER_SCHEMA_VERIFIED = "ledger_schema_verified"
    LEDGER_VERSION_DRIFT = "ledger_version_drift"


_LEVEL_BY_EVENT: dict[AuditEventType, str] = {
    # ... existing entries unchanged ...
    AuditEventType.LEDGER_SCHEMA_VERIFIED: "info",
    AuditEventType.LEDGER_VERSION_DRIFT: "warn",
}
```

### Unit Tests

- `tests/test_audit_log_ledger_event_types.py` (**new**):
  - `test_audit_event_type_includes_ledger_schema_verified` — `AuditEventType.LEDGER_SCHEMA_VERIFIED.value == "ledger_schema_verified"`.
  - `test_audit_event_type_includes_ledger_version_drift` — `AuditEventType.LEDGER_VERSION_DRIFT.value == "ledger_version_drift"`.
  - `test_emit_ledger_schema_verified_renders_at_info_level` — capture stderr; invoke `emit(LEDGER_SCHEMA_VERIFIED, status="match", ...)`; parse JSON line; assert `level == "info"` and `event_type == "ledger_schema_verified"`.
  - `test_emit_ledger_version_drift_renders_at_warn_level` — same shape; assert `level == "warn"` and `event_type == "ledger_version_drift"`.
  - `test_emit_ledger_version_drift_passes_warn_filter_when_min_level_is_warn` — set `BICAMERAL_AUDIT_LOG_LEVEL=warn`; invoke `emit(LEDGER_VERSION_DRIFT, ...)`; assert record is emitted to stderr.
  - `test_emit_ledger_schema_verified_dropped_when_min_level_is_warn` — set `BICAMERAL_AUDIT_LOG_LEVEL=warn`; invoke `emit(LEDGER_SCHEMA_VERIFIED, ...)`; assert nothing on stderr (info-level event dropped).
  - `test_audit_event_type_test_compliance_policy_docs_taxonomy_test_passes` — re-invoke the existing `tests/test_compliance_policy_docs.py::test_audit_log_policy_doc_documents_event_taxonomy` fixture and verify both new event types appear in `docs/policies/audit-log.md`. **Implementer note**: this test will FAIL after Phase 2 lands because the policy doc isn't updated until Layer 4. Skip this test or mark `xfail` for v1 until Layer 4 ships; do NOT update the policy doc here (defer to Layer 4 to avoid two-PR doc churn for the same surface).

## CI Commands

- `pytest tests/test_ledger_schema_meta_wire_format.py tests/test_ledger_schema_meta_migration.py -v` — Phase 1 (wire-format sentinel + migration).
- `pytest tests/test_audit_log_ledger_event_types.py -v` — Phase 2 (enum extension + level mapping).
- `pytest tests/test_audit_log_*.py tests/test_ledger_schema_meta_*.py tests/test_phase2_ledger.py -v` — full audit-log + ledger-schema regression.
- `pytest tests/ -k "ledger or audit_log" -q` — broader regression across any ledger-or-audit-log-touching test (~218 tests).
- `ruff check audit_log.py ledger/schema.py tests/test_audit_log_ledger_event_types.py tests/test_ledger_schema_meta_*.py` — lint clean on every touched + new file.
- `ruff format --check audit_log.py ledger/schema.py tests/test_audit_log_ledger_event_types.py tests/test_ledger_schema_meta_*.py` — format clean.

## Implementer notes

- The `option<string>` / `option<datetime>` types with `DEFAULT NONE` are intentional — existing v15 ledgers upgraded to v16 won't have these fields populated until the FIRST `init_schema()` call after the binary upgrade. The "first-write" status returned by `_write_wire_format_sentinel` correctly captures that case.
- `importlib.metadata.version("surrealdb")` is the canonical way to read the installed package version at runtime; do NOT use `surrealdb.__version__` (the package doesn't expose it as confirmed by Layer 1 investigation).
- The audit-log emit inside `init_schema` must be wrapped in a try/except per the existing audit_log discipline ("audit log MUST NOT break callers"); Phase 1 changes locks this with `test_init_schema_audit_log_emit_failure_does_not_break_boot`.
- The `_migrate_v15_to_v16` body is intentionally a no-op — the new field DEFINEs are applied via `init_schema`'s OVERWRITE-pass on `_META`. The migration registry entry exists so `SchemaVersionTooNew` is not raised against existing v15 ledgers when v16-aware code starts.
- The deferred policy-doc update is documented at the test layer (the `xfail` marker in `test_audit_log_ledger_event_types.py`) so a future Layer 4 reviewer can grep for the marker and remove it as part of that PR's deliverable.
