# Changelog

All notable changes to bicameral-mcp are tracked here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.4.5 — 2026-04-14

### Fixed

- **Ingest now stamps a baseline `content_hash` at HEAD for every grounded
  region.** Previously `ingest_payload` only computed a hash when the caller
  explicitly passed `commit_hash` in the payload, which the MCP `bicameral_ingest`
  handler never did — so every bulk transcript ingest persisted empty hashes
  and decisions were permanently stuck in `pending`. Now `ingest_payload`
  resolves HEAD from `repo_path` when no commit_hash is supplied, computes a
  baseline hash for every region, and derives the intent's initial status from
  that hash. Freshly ingested decisions are born `reflected` when their
  grounded code exists at HEAD.
- **Empty-hash regions from older ledgers are now self-healed.** `handle_link_commit`
  runs a repo-scoped backfill sweep before the normal drift loop, walking any
  code regions with an empty `content_hash` and handing them to
  `HashDriftAnalyzer`, which adopts the current git state as the baseline and
  flips the owning intents to `reflected`. No forced migration, no new tooling —
  just call `bicameral_status` or any other handler that triggers a
  `link_commit` and legacy ledgers heal themselves.
- **`HashDriftAnalyzer.analyze_region` self-heals missing baselines.** When
  `stored_hash == ""` and the analyzer can compute a real hash at the requested
  ref, it returns `reflected` with the new hash as the baseline instead of the
  old `ungrounded` verdict. Used by both the new backfill path and the regular
  drift sweep.

### Added

- Per-region status aggregation on intents: an intent with multiple code
  regions now adopts the loudest status across them (drifted > reflected >
  pending > ungrounded), so a single drifted region always raises an alarm
  even if other regions still reflect.
- `SurrealDBLedgerAdapter.backfill_empty_hashes(repo_path, drift_analyzer=...)`
  — public method to run the backfill sweep on demand. Idempotent and scoped
  by repo, so multi-repo SurrealDB instances stay isolated.
- `ledger.queries.get_regions_without_hash(client, repo="")` — helper query
  used by the backfill sweep to find legacy regions.
- New test module `tests/test_phase1_l1_wiring.py` with four regression
  scenarios: ingest→reflected, edit→drifted, phantom range→not reflected,
  and legacy empty-hash backfill→reflected.

### Migration

No manual action required. Existing ledgers backfill themselves on the next
`bicameral_status`, `bicameral_link_commit`, or any other tool that drives a
`link_commit`. Users whose files aren't touched by subsequent commits can
also simply re-run their original bulk ingest — v0.4.5 stamps hashes at ingest
time, so re-ingestion produces correct status for every grounded decision
without any further work.

---

## 0.4.4 — 2026-04-13

- Submodule bump for grounding reuse + coverage loop (Phase 3 of the
  code-locator drift fix plan).

## 0.4.3 — 2026-04-12

- Few-shot `bicameral-ingest` skill update (`ff2eff7`).

## 0.4.2 — 2026-04-11

- Skills bundle + CLAUDE.md context files.

## 0.4.1 — 2026-04-10

- Ingest pipeline hardening: input contracts, payload normalization, freshness
  guards.

## 0.4.0 — 2026-04-08

- Event-sourced collaboration + BicameralContext request-scoped snapshot
  isolation.
