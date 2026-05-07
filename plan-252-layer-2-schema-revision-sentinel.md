# Plan: #252 Layer 2 — schema-revision sentinel for surrealdb-py wire-format awareness (round 2)

**change_class**: feature

**doc_tier**: standard

**terms_introduced**:
- term: wire-format sentinel
  home: ledger/schema.py
- term: bicameral_meta (table)
  home: ledger/schema.py
- term: surrealdb-py client version (recorded)
  home: ledger/schema.py
- term: ledger_schema_verified (audit-log event)
  home: audit_log.py
- term: ledger_version_drift (audit-log event)
  home: audit_log.py

**boundaries**:
- limitations: WARN-only posture per operator directive ("gating is observability, not obstruction"). The sentinel records `surrealdb_client_version_at_first_write` + `surrealdb_client_version_at_last_write` + `last_write_at` and emits an audit-log event on every server connect. Drift between the recorded "last write" version and the running version emits `ledger_version_drift` at warn level — does NOT fail-fast. The actual breaking case ("Invalid revision N for type Value") already fail-fasts at the SurrealDB deserialization layer; Layer 2's value is identifying the cause via structured emission, not adding a second gate. Hard gating is reserved for Layer 5 (opt-in auto-migrate) where it pairs with a migration path.
- non_goals: do not add a `--strict` mode in v1 that converts the WARN to a fail-fast — operator directive forbids that escalation in Layer 2 specifically; it belongs in Layer 5 where the operator opts into the gating contract. Do not migrate the existing `schema_meta.version` (bicameral SQL schema) — that's a separate dimension already managed by the existing migration framework. Do not introduce a "minimum surrealdb-py version" enforcement; the pin in pyproject.toml (#252 Layer 1) handles the enforcement at install time, and runtime should not double-gate.
- exclusions: not modifying any `_migrate_vN_to_vM` function (the v15→v16 entry exists but its body is a no-op — the new `bicameral_meta` table's DEFINEs are applied via init_schema's OVERWRITE pass). Not modifying `_set_schema_version` or `_get_schema_version` — `bicameral_meta` is a separate table with its own persistence semantics, deliberately decoupled from `schema_meta`'s nuke-and-recreate pattern (round-1 audit's central finding). Not modifying `audit_log.py`'s public `emit()` or `JsonFormatter` — only adding two new `AuditEventType` enum values + their level mapping. Layer 2 DOES update `docs/policies/audit-log.md`'s event-taxonomy table inline so the existing `tests/test_compliance_policy_docs.py::test_audit_log_policy_doc_documents_event_taxonomy` doc/code drift lock from #227 remains satisfied (round-1 audit's advisory finding; supersedes the prior "defer to Layer 4" approach).

## Open Questions

All resolved during /qor-plan dialogue + round-1 audit feedback (2026-05-07):

- **Sentinel location** (round 1: c "extend schema_meta"; **round 2: b "new dedicated `bicameral_meta` table"**): the round-1 audit identified that `schema_meta` is DELETEd on every `_set_schema_version` call (`ledger/schema.py:924-930`), which would wipe wire-format fields on every migration cycle. Round-2 amendment uses a separate `bicameral_meta` table with stable persistence semantics — clean separation from the existing nuke-and-recreate `schema_meta` pattern.
- **Sentinel content shape** (option b — unchanged): `surrealdb_client_version_at_first_write` (immutable after first set) + `surrealdb_client_version_at_last_write` (updated on every server connect) + `last_write_at` (datetime). Minimum useful for diagnosing drift.
- **Mismatch response** (option b — unchanged): WARN + audit-log emit; proceed. Per operator directive.
- **When does the check run** (round 1: in `init_schema()`; **round 2: at the end of `adapter.connect()` AFTER both `init_schema()` and `migrate()` return**): the round-1 audit identified that `init_schema()` runs BEFORE `migrate()` (per `migrate()`'s docstring at line 936), so a sentinel write inside `init_schema` would precede migrations. Round-2 amendment moves the sentinel call to `adapter.connect()` after the migrate try/except completes successfully — the sentinel reflects the post-migration state.
- **Audit-log event types** (option a — unchanged): two new `AuditEventType` enum values: `LEDGER_SCHEMA_VERIFIED` (info-level, fires on match) + `LEDGER_VERSION_DRIFT` (warn-level, fires on mismatch).

## Phase 1: new `bicameral_meta` table + write-on-connect helper

### Affected Files

- `tests/test_ledger_bicameral_meta_wire_format.py` — **new** functionality tests for the new `bicameral_meta` table's row write/read semantics
- `tests/test_ledger_bicameral_meta_migration.py` — **new** functionality tests for the v15→v16 migration that introduces the new `bicameral_meta` table on existing ledgers
- `ledger/schema.py` — add `_BICAMERAL_META` constant (separate from existing `_META`); wire it into `init_schema`'s define-pass loop; add helper `_write_wire_format_sentinel(client)`; bump `SCHEMA_VERSION` to 16; add no-op `_migrate_v15_to_v16`
- `ledger/adapter.py` — invoke `_write_wire_format_sentinel(client)` at the end of `connect()` after both `init_schema()` and `migrate()` return successfully; emit the resulting audit-log event

### Changes

**`ledger/schema.py`** — bump `SCHEMA_VERSION` and define the new table:

```python
SCHEMA_VERSION = 16

SCHEMA_COMPATIBILITY: dict[int, str] = {
    # ... existing entries unchanged ...
    16: "0.13.x",  # #252 Layer 2 — wire-format sentinel; placeholder, release-eng pins final value at PR merge
}
```

```python
# #252 Layer 2 — wire-format sentinel.
# Separate from `_META` (schema_meta) because schema_meta is DELETEd on every
# `_set_schema_version` call. bicameral_meta has stable persistence semantics:
# write-once for `at_first_write`, update-each-connect for `at_last_write`.
_BICAMERAL_META = [
    "DEFINE TABLE bicameral_meta SCHEMAFULL",
    "DEFINE FIELD surrealdb_client_version_at_first_write ON bicameral_meta TYPE option<string> DEFAULT NONE",
    "DEFINE FIELD surrealdb_client_version_at_last_write  ON bicameral_meta TYPE option<string> DEFAULT NONE",
    "DEFINE FIELD last_write_at                            ON bicameral_meta TYPE option<datetime> DEFAULT NONE",
]
```

`init_schema` extension — append `_BICAMERAL_META` to the define-pass loop:

```python
async def init_schema(client: LedgerClient) -> None:
    # ... existing docstring unchanged ...
    for sql in _ANALYZERS + _TABLES + _EDGES + _META + _BICAMERAL_META:
        sql = sql.strip()
        if sql:
            await _execute_define_idempotent(client, _with_overwrite(sql))
```

**`ledger/schema.py`** — new `_migrate_v15_to_v16` (no-op body; registry entry only):

```python
async def _migrate_v15_to_v16(client: LedgerClient) -> None:
    """#252 Layer 2: introduce bicameral_meta table for wire-format sentinel.

    No data migration required — the new `bicameral_meta` DEFINEs are
    applied via init_schema's OVERWRITE pass on every connect. The
    first-write value is populated by `_write_wire_format_sentinel` at
    the end of `adapter.connect()`. Existing v15 ledgers transitioning
    to v16 will record the upgrading binary's version as the
    `at_first_write` because we have no archaeological record of which
    surrealdb-py version originally wrote them. Documented in the field
    docstring.

    This migration body is intentionally empty; the registry entry
    exists so the migration loop in `migrate(client)` sees v15→v16 as a
    known forward path (avoids `SchemaVersionTooNew` on existing v15
    ledgers when v16-aware code starts).
    """
    return
```

Add to the migration registry:

```python
_MIGRATIONS: dict[int, Callable[[LedgerClient], Awaitable[None]]] = {
    # ... existing entries 5..15 unchanged ...
    16: _migrate_v15_to_v16,
}
```

**`ledger/schema.py`** — new sentinel helper:

```python
async def _write_wire_format_sentinel(client: LedgerClient) -> tuple[str | None, str | None, str]:
    """Read and update the `bicameral_meta` row with the running surrealdb-py
    version.

    Returns ``(recorded, running, status)`` where ``status`` is one of
    ``"first-write"`` (no prior recorded version), ``"match"`` (recorded
    equals running), or ``"drift"`` (recorded differs from running).

    Side effects: updates ``surrealdb_client_version_at_last_write`` and
    ``last_write_at`` on the singleton ``bicameral_meta`` row. Sets
    ``surrealdb_client_version_at_first_write`` only when it was previously
    NONE (immutable after first set). If the table has no row yet (fresh
    ledger), CREATEs the singleton row with both first/last fields set to
    the running version.

    The caller (adapter.connect) is responsible for emitting the audit-log
    event based on the returned status; this helper does not import
    audit_log to keep the ledger module's dependency surface tight.
    """
    import importlib.metadata

    try:
        running = importlib.metadata.version("surrealdb")
    except importlib.metadata.PackageNotFoundError:
        running = "unknown"

    rows = await client.query("SELECT * FROM bicameral_meta LIMIT 1")
    if not rows:
        await client.query(
            "CREATE bicameral_meta SET "
            "surrealdb_client_version_at_first_write = $running, "
            "surrealdb_client_version_at_last_write = $running, "
            "last_write_at = time::now()",
            {"running": running},
        )
        return None, running, "first-write"

    row = rows[0]
    recorded = row.get("surrealdb_client_version_at_last_write")
    first = row.get("surrealdb_client_version_at_first_write")

    if first is None:
        await client.query(
            "UPDATE bicameral_meta SET "
            "surrealdb_client_version_at_first_write = $running, "
            "surrealdb_client_version_at_last_write = $running, "
            "last_write_at = time::now()",
            {"running": running},
        )
        return recorded, running, "first-write"

    await client.query(
        "UPDATE bicameral_meta SET "
        "surrealdb_client_version_at_last_write = $running, "
        "last_write_at = time::now()",
        {"running": running},
    )

    if recorded == running:
        return recorded, running, "match"
    return recorded, running, "drift"
```

**`ledger/adapter.py`** — invoke the sentinel + emit audit-log event at the end of `connect()`:

```python
async def connect(self) -> None:
    # ... existing body through init_schema + migrate try/except unchanged ...
    # (Existing code that sets self._connected = True on success path stays as-is.)

    # #252 Layer 2 — wire-format sentinel + observability emit. Runs after
    # both init_schema() and migrate() complete successfully. On the
    # DestructiveMigrationRequired path, `connect()` returns early via the
    # existing exception handler and this block is skipped — operators see
    # the destructive-migration-pending warning instead, which is the
    # higher-priority signal at that moment.
    from .schema import _write_wire_format_sentinel

    try:
        recorded, running, status = await _write_wire_format_sentinel(self._client)
    except Exception:  # noqa: BLE001 — observability MUST NOT break connect
        return

    try:
        from audit_log import AuditEventType
        from audit_log import emit as audit_emit

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
    except Exception:  # noqa: BLE001 — audit_log MUST NOT break connect
        pass
```

(The `SCHEMA_VERSION` import is already present in `ledger/adapter.py:61` via `from .schema import DestructiveMigrationRequired, init_schema, migrate`; extend the import to include `SCHEMA_VERSION` and `_write_wire_format_sentinel`.)

### Unit Tests

- `tests/test_ledger_bicameral_meta_wire_format.py` (**new**):
  - `test_write_wire_format_sentinel_creates_row_on_empty_table` — invoke `_write_wire_format_sentinel` against an in-memory ledger with `bicameral_meta` defined but no row; assert returned status is `"first-write"`, `recorded is None`; query the table and assert the new row's `at_first_write == at_last_write == running` and `last_write_at` is non-NONE.
  - `test_write_wire_format_sentinel_returns_running_version_from_importlib_metadata` — monkeypatch `importlib.metadata.version` to return `"2.0.0-test"`; invoke against empty table; assert the returned `running` and the persisted row both equal `"2.0.0-test"`.
  - `test_write_wire_format_sentinel_runs_unknown_branch_when_package_missing` — monkeypatch `importlib.metadata.version` to raise `PackageNotFoundError`; invoke against empty table; assert returned `running == "unknown"` and the helper does not raise; persisted row has `at_first_write == "unknown"`.
  - `test_write_wire_format_sentinel_preserves_first_write_on_subsequent_calls` — invoke twice with monkeypatched versions `"2.0.0"` then `"2.1.0"`; assert post-second-call the persisted row has `at_first_write == "2.0.0"` and `at_last_write == "2.1.0"`.
  - `test_write_wire_format_sentinel_returns_match_when_versions_equal` — pre-populate the row with `at_last_write="2.0.0"` and `at_first_write="2.0.0"`; monkeypatch the running version to `"2.0.0"`; assert returned status is `"match"`.
  - `test_write_wire_format_sentinel_returns_drift_when_versions_differ` — pre-populate with `at_last_write="2.0.0"`, `at_first_write="2.0.0"`; monkeypatch running to `"2.1.0"`; assert returned status is `"drift"` with `recorded == "2.0.0"` and `running == "2.1.0"`.
  - `test_write_wire_format_sentinel_returns_first_write_when_at_first_write_is_none_but_row_exists` — pre-populate with `at_last_write="2.0.0"` and `at_first_write=None` (edge case: row exists from a partial init); monkeypatch running to `"2.0.0"`; assert returned status is `"first-write"` and `at_first_write` is set to running afterwards.
  - `test_adapter_connect_emits_ledger_schema_verified_on_first_write` — patch `audit_log.emit` with a capture stub; instantiate adapter with `memory://` URL; call `await adapter.connect()`; assert one `LEDGER_SCHEMA_VERIFIED` emit with `status="first-write"` and `surrealdb_client_version_running` set.
  - `test_adapter_connect_emits_ledger_version_drift_on_recorded_mismatch` — same shape, but pre-populate `bicameral_meta` with `at_last_write="2.0.0"` then monkeypatch the running version to `"2.1.0"`; assert one `LEDGER_VERSION_DRIFT` emit with the expected fields.
  - `test_adapter_connect_audit_log_emit_failure_does_not_break_connect` — monkeypatch `audit_log.emit` to raise; call `await adapter.connect()`; assert connect returns normally (no exception propagated) and the bicameral_meta row was written correctly.
  - `test_adapter_connect_sentinel_helper_failure_does_not_break_connect` — monkeypatch `_write_wire_format_sentinel` to raise; call `await adapter.connect()`; assert connect returns normally and `self._connected` is True.

- `tests/test_ledger_bicameral_meta_migration.py` (**new**):
  - `test_migrate_v15_to_v16_is_no_op_for_existing_v15_ledger` — pre-populate `schema_meta.version=15`; run `migrate(client)`; assert post-migrate `schema_meta.version == 16` and `bicameral_meta` row is empty (the sentinel writes only happen in `adapter.connect`, not in the migration body).
  - `test_migration_registry_includes_v15_to_v16` — assert `_MIGRATIONS[16] is _migrate_v15_to_v16` (catalog membership lock; same shape as canary catalog version pin).
  - `test_init_schema_creates_bicameral_meta_table` — invoke `init_schema(client)` on a fresh in-memory ledger; query `INFO FOR TABLE bicameral_meta` (or equivalent existence check via `SELECT * FROM bicameral_meta LIMIT 1` returning `[]` without error); assert the table exists. (Adjust query if `INFO FOR TABLE` returns empty under embedded mode per CLAUDE.md `pilot/mcp/CLAUDE.md` v2-quirks note — fall back to a successful empty-result `SELECT` as the existence proof.)

## Phase 2: AuditEventType enum extension + level mapping + policy-doc taxonomy update

### Affected Files

- `tests/test_audit_log_ledger_event_types.py` — **new** functionality tests for the two new enum values + their level-table entries
- `audit_log.py` — add two new `AuditEventType` enum values (`LEDGER_SCHEMA_VERIFIED`, `LEDGER_VERSION_DRIFT`) + their entries in `_LEVEL_BY_EVENT`
- `docs/policies/audit-log.md` — extend the event-taxonomy table with two new rows for `ledger_schema_verified` (info; source: `adapter.connect()` after init_schema + migrate; fields: `surrealdb_client_version_running`, `bicameral_schema_version`, `status`) and `ledger_version_drift` (warn; same source; fields: `surrealdb_client_version_recorded`, `surrealdb_client_version_running`, `bicameral_schema_version`)

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

**`docs/policies/audit-log.md`** — extend the event-taxonomy table at `## Event taxonomy` with two new rows (after `error`):

```markdown
| `ledger_schema_verified` | info | `adapter.connect()` after `init_schema` + `migrate` (#252 Layer 2) | `surrealdb_client_version_running`, `bicameral_schema_version`, `status` (`first-write` / `match`) |
| `ledger_version_drift` | warn | `adapter.connect()` after `init_schema` + `migrate` (#252 Layer 2) | `surrealdb_client_version_recorded`, `surrealdb_client_version_running`, `bicameral_schema_version` |
```

This keeps the existing `tests/test_compliance_policy_docs.py::test_audit_log_policy_doc_documents_event_taxonomy` content-contract test (added in #227) passing — the test asserts every `AuditEventType.value` appears in the policy doc; both new values appear in the doc with this update.

### Unit Tests

- `tests/test_audit_log_ledger_event_types.py` (**new**):
  - `test_audit_event_type_includes_ledger_schema_verified` — `AuditEventType.LEDGER_SCHEMA_VERIFIED.value == "ledger_schema_verified"`.
  - `test_audit_event_type_includes_ledger_version_drift` — `AuditEventType.LEDGER_VERSION_DRIFT.value == "ledger_version_drift"`.
  - `test_emit_ledger_schema_verified_renders_at_info_level` — capture stderr; invoke `emit(LEDGER_SCHEMA_VERIFIED, status="match", surrealdb_client_version_running="2.0.0", bicameral_schema_version=16)`; parse JSON line; assert `level == "info"` and `event_type == "ledger_schema_verified"` and the three structured fields present.
  - `test_emit_ledger_version_drift_renders_at_warn_level` — same shape with `LEDGER_VERSION_DRIFT`; assert `level == "warn"` and `event_type == "ledger_version_drift"`.
  - `test_emit_ledger_version_drift_passes_warn_filter_when_min_level_is_warn` — set `BICAMERAL_AUDIT_LOG_LEVEL=warn`; invoke `emit(LEDGER_VERSION_DRIFT, ...)`; assert record is emitted to stderr.
  - `test_emit_ledger_schema_verified_dropped_when_min_level_is_warn` — set `BICAMERAL_AUDIT_LOG_LEVEL=warn`; invoke `emit(LEDGER_SCHEMA_VERIFIED, ...)`; assert nothing on stderr (info-level event dropped).
  - `test_audit_log_policy_doc_documents_new_event_types` — invoke the existing `tests/test_compliance_policy_docs.py::test_audit_log_policy_doc_documents_event_taxonomy` (or replicate its assertion locally) and verify `ledger_schema_verified` and `ledger_version_drift` both appear in `docs/policies/audit-log.md`. This is the doc/code drift lock; it MUST pass after Phase 2 lands.

## CI Commands

- `pytest tests/test_ledger_bicameral_meta_wire_format.py tests/test_ledger_bicameral_meta_migration.py -v` — Phase 1 (sentinel helper + table init + migration registry).
- `pytest tests/test_audit_log_ledger_event_types.py tests/test_compliance_policy_docs.py -v` — Phase 2 (enum extension + level mapping + policy-doc content-contract).
- `pytest tests/test_audit_log_*.py tests/test_ledger_bicameral_meta_*.py tests/test_phase2_ledger.py -v` — full audit-log + ledger regression.
- `pytest tests/ -k "ledger or audit_log" -q` — broader regression across any ledger-or-audit-log-touching test (~218 tests baseline).
- `ruff check audit_log.py ledger/schema.py ledger/adapter.py tests/test_audit_log_ledger_event_types.py tests/test_ledger_bicameral_meta_*.py` — lint clean on every touched + new file.
- `ruff format --check audit_log.py ledger/schema.py ledger/adapter.py tests/test_audit_log_ledger_event_types.py tests/test_ledger_bicameral_meta_*.py` — format clean.

## Implementer notes

- The new `bicameral_meta` table is deliberately separate from `schema_meta` to avoid the `_set_schema_version` DELETE+CREATE pattern that wipes the latter on every migration step. Do NOT consolidate them in a future refactor without first replacing `_set_schema_version`'s implementation with an UPDATE-style upsert.
- `option<datetime>` syntax should be smoke-tested against an in-memory `surrealkv://` or `memory://` fixture before relying on it. Existing schema uses `option<string>` and `option<object>`; `option<datetime>` is the same form with a different inner type and is expected to work in SurrealDB v2 — but the smoke test removes the assumption. If unsupported, the field becomes `datetime DEFAULT time::now()` (non-option) and the helper writes the field unconditionally.
- `importlib.metadata.version("surrealdb")` is the canonical way to read the installed package version at runtime; do NOT use `surrealdb.__version__` (the package doesn't expose it as confirmed by Layer 1 investigation).
- The audit-log emit inside `adapter.connect()` is wrapped in two layers of try/except: one around `_write_wire_format_sentinel` (so a sentinel-helper exception doesn't break connect), and one around the audit_log emit itself (so a stubbed-out emit raise doesn't propagate). Both layers are required by the existing audit_log discipline ("audit log MUST NOT break callers"); locked by `test_adapter_connect_audit_log_emit_failure_does_not_break_connect` and `test_adapter_connect_sentinel_helper_failure_does_not_break_connect`.
- The `_migrate_v15_to_v16` body is intentionally a no-op — the `bicameral_meta` table's DEFINEs are applied via `init_schema`'s OVERWRITE-pass on `_BICAMERAL_META`. The migration registry entry exists so `SchemaVersionTooNew` is not raised against existing v15 ledgers when v16-aware code starts.
- `client.query(sql, vars)` takes `vars` as the second positional parameter (per `ledger/client.py:150`). The plan code passes the dict positionally, matching the existing `_set_schema_version` convention. Do NOT use `bindings` as a kwarg — it doesn't exist on the client API.
