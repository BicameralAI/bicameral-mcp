# Plan: ledger equality key indexes for symbol.name + vocab_cache.(query_text, repo)

**change_class**: hotfix

**doc_tier**: standard

**boundaries**:

- limitations: New indexes are **non-unique**. They accelerate `WHERE field = $x`
  lookups but do not enforce single-row-per-key. The UPSERT call sites
  (`upsert_symbol`, `vocab_cache` UPSERT in `ledger/queries.py`) read the first
  matching row, which implies a single-row-per-key invariant that the absence
  of a UNIQUE index has historically failed to enforce. Tightening to UNIQUE
  would require a backfill that merges or drops existing duplicates; per
  `docs/DEV_CYCLE.md` §4.7.1 that is a destructive operation and belongs in a
  dedicated commit + later release. Out of scope here.
- non_goals: Enforcing single-row-per-name uniqueness on `symbol`. Compound
  index optimisation for the `code_region`, `input_span`, and `subject_version`
  UPSERT-WHERE sites (those already match an existing index prefix). Raising
  the `BICAMERAL_QUERY_TIMEOUT_READ_SECONDS` default. Auditing UPSERT call
  sites outside `ledger/queries.py`.
- exclusions: Deduplication or migration of existing `symbol` or `vocab_cache`
  rows. The new indexes are additive; rows valid before this migration remain
  valid after it.

## Open Questions

- None. The audit narrowed the scope to two indexes; both follow the same
  pattern. The migration function below is a safety belt — `init_schema` is
  the actual mechanism that defines the new indexes on connect.

## Context (read-only — do not include in the implementation diff)

- The originating timeout: `bicameral-mcp reset --confirm --wipe-mode=ledger
  --replay-from-events` against a 3,002-event log fails near the end of replay
  with `LedgerTimeoutError: Ledger query exceeded read timeout (5.61s > 5.0s
  budget): UPSERT symbol SET ... WHERE name = $name`. The `symbol` table has
  `idx_sym_name` defined as `SEARCH ANALYZER ... BM25`, which accelerates
  `name @0@ $query` semantic match but **cannot** accelerate `name = $name`
  equality lookup. The UPSERT falls back to a full table scan; latency grows
  linearly with table size; by the ~2,500th symbol the per-call cost exceeds
  the 5.0s read budget.
- `vocab_cache` has the identical pattern: `idx_vocab_query` is BM25 only,
  yet `ledger/queries.py:419` runs `UPSERT vocab_cache SET ... WHERE
  query_text = $query_text AND repo = $repo`. This issue has not produced a
  caller-visible failure yet, but the same O(n) scan applies and the
  recovery path #410 advertises will surface it on any sufficiently large
  vocab cache.
- The fix is structurally tied to PR #412 (the #410 recovery path): without
  it the resolver fix is correct but `--replay-from-events` end-to-end still
  fails. Filing as a separate PR after #412 lands would ship a recovery path
  that times out for every user with a real-size events log.
- Per `docs/DEV_CYCLE.md` §4.7.1 these are additive `DEFINE INDEX` operations
  in the ✅ Allowed column. Per §4.7.2 the carve-out applies — this is an
  invariant fix, not new feature surface, so no flag-gate is required.

## Phase 1: Add equality key indexes alongside the existing BM25 indexes

### Affected Files

- `ledger/schema.py` — bump `SCHEMA_VERSION` 24 → 25; add `SCHEMA_COMPATIBILITY[25]` entry; add two `DEFINE INDEX` entries to the schema definitions list; define `_migrate_v24_to_v25` as a safety-belt that re-issues the indexes via `_execute_define_idempotent`; register `25: _migrate_v24_to_v25` in `_MIGRATIONS`.
- `tests/test_schema_index_lookup_perf.py` — new file (sociable).
- `tests/test_phase2_ledger.py` — no edits; existing schema-version assertions update naturally once `SCHEMA_VERSION` bumps. Listed only as a re-run target.

### Changes

`ledger/schema.py` deltas (no surrounding code rewrites):

1. Line 31: `SCHEMA_VERSION = 24` → `SCHEMA_VERSION = 25`.

2. `SCHEMA_COMPATIBILITY` map gains:
   ```python
   25: "0.15.x",  # equality key indexes on symbol.name + vocab_cache.(query_text, repo); release-eng pins final value at PR merge
   ```

3. In the `symbol`-table block (currently lines ~195–202), append after the
   existing `idx_sym_name` BM25 line and `idx_sym_file` line:
   ```python
   "DEFINE INDEX idx_sym_name_lookup ON symbol FIELDS name",
   ```
   The BM25 `idx_sym_name` stays as-is — `code-locator` reads against it via
   `name @0@ $query` and that path is unchanged.

4. In the `vocab_cache`-table block (currently lines ~223–224), append:
   ```python
   "DEFINE INDEX idx_vocab_query_lookup ON vocab_cache FIELDS query_text, repo",
   ```
   The compound shape matches the WHERE in `ledger/queries.py:419` exactly.
   The existing BM25 `idx_vocab_query` and single-field `idx_vocab_repo` stay
   — they serve other read paths.

5. New migration function near line ~1471 (pattern lifted from
   `_migrate_v23_to_v24`):
   ```python
   async def _migrate_v24_to_v25(client: LedgerClient) -> None:
       """v24 → v25: Add equality key indexes for UPSERT-WHERE lookups.

       Pre-v25 the `symbol.name` and `vocab_cache.query_text` columns were
       indexed only via SEARCH/BM25, which accelerates `@0@` matches but not
       `WHERE field = $value` equality lookups. The UPSERT call sites in
       `ledger/queries.py` (`upsert_symbol`, `vocab_cache` UPSERT) fell back
       to full table scans, producing O(n) per-call latency that crossed the
       5.0s read timeout near the end of large `reset --replay-from-events`
       runs (#410 dogfood).

       Both indexes are additive and non-unique — rows valid under the old
       schema remain valid post-migration. `init_schema` re-issues every
       DEFINE on connect; this migration is the version-boundary safety belt
       that runs the OVERWRITE explicitly even when init_schema's pass is
       interrupted. Idempotent via `_execute_define_idempotent`.
       """
       await _execute_define_idempotent(
           client,
           "DEFINE INDEX OVERWRITE idx_sym_name_lookup ON symbol FIELDS name",
       )
       await _execute_define_idempotent(
           client,
           "DEFINE INDEX OVERWRITE idx_vocab_query_lookup ON vocab_cache "
           "FIELDS query_text, repo",
       )
   ```

6. `_MIGRATIONS` registry gains:
   ```python
   25: _migrate_v24_to_v25,
   ```

### Unit Tests

- `tests/test_schema_index_lookup_perf.py` (new) — sociable; instantiates a
  real `LedgerClient` over `memory://`, runs `init_schema` + `migrate`, then
  exercises the two UPSERT paths and verifies query-plan selection via
  SurrealDB 2.x's trailing `EXPLAIN` modifier:

  - `test_upsert_symbol_returns_single_row_for_unique_name` — seeds the
    `symbol` table with 1,000 rows of synthetic names, calls
    `upsert_symbol(client, name="unique_marker_x", file_path="…")`, asserts
    the call returns a non-empty `id` string AND the table contains exactly
    one row matching `name = "unique_marker_x"`. Pins observable behaviour
    (right row returned, no duplicates).

  - `test_upsert_vocab_cache_returns_single_row_for_unique_compound_key` —
    same shape against `vocab_cache`; seeds 1,000 rows, runs the UPSERT on a
    novel `(query_text, repo)` pair, asserts a single matching row exists
    and the call returned successfully.

  - `test_symbol_name_lookup_uses_equality_index_post_migration` — runs
    `init_schema` + `migrate` on a fresh `memory://` client, then issues
    `SELECT * FROM symbol WHERE name = $name EXPLAIN`. Parses the returned
    plan rows and asserts the first row's `operation == "Iterate Index"`
    with `detail.index == "idx_sym_name_lookup"`. Empirically validated:
    pre-migration the same query plans to `operation: "Iterate Table"`
    (full scan) because `idx_sym_name` is BM25-only and cannot accelerate
    equality. Post-migration the new equality index is selected. The
    EXPLAIN modifier works in embedded mode (verified against
    `memory://`) — the failure mode the test catches is precisely:
    `_migrate_v24_to_v25` runs without exception but the DEFINE INDEX
    did not land (the plan stays at `Iterate Table`, the assertion fails
    loudly).

  - `test_vocab_cache_lookup_uses_compound_index_post_migration` — same
    pattern: `SELECT * FROM vocab_cache WHERE query_text = $q AND repo = $r EXPLAIN`,
    assert `operation == "Iterate Index"` with `detail.index ==
    "idx_vocab_query_lookup"`.

  - `test_schema_version_advances_to_25` — runs `init_schema` + `migrate` on
    a fresh adapter, reads `schema_meta.version`, asserts it equals `25`.
    Loud failure when `SCHEMA_VERSION` and registry drift.

  All five tests are sociable per the project's mandate (`pilot/mcp/CLAUDE.md`
  § "Sociable Testing for UX Paths"). No `MagicMock`; no row-dict
  hand-crafting; the real adapter writes through the real schema. The
  EXPLAIN-based assertions deterministically detect a silently-broken
  migration — without the new indexes the query plan reports
  `Iterate Table`, which fails the assertion regardless of whether the
  migration function returned without exception.

## CI Commands

- `pytest tests/test_schema_index_lookup_perf.py -q` — the five new sociable tests above.
- `pytest tests/test_phase2_ledger.py tests/test_replay_determinism.py tests/test_team_event_replay.py -q` — existing ledger + replay regression suite; must stay green across the schema bump.
- `pytest tests/test_reset_cli_410.py -q` — confirms the PR's resolver fix still passes alongside the schema change.
- `ruff check ledger/schema.py tests/test_schema_index_lookup_perf.py` — lint.
- `ruff format --check ledger/schema.py tests/test_schema_index_lookup_perf.py` — format.
