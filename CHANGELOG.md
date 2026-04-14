# Changelog

All notable changes to bicameral-mcp are tracked here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.4.6 — 2026-04-14 — Adoption Floor (Trust + First Wow)

Five initiatives: FC-1 BM25 degeneracy guard, FC-2 multi-region grounding
via graph-channel fusion, authoritative-ref + pollution guards at both write
sites, `bicameral.brief` pre-meeting one-pager, and `bicameral.reset` fail-safe
valve.

### Added

- **`bicameral.brief(topic, participants?)`** — Pre-meeting one-pager generator.
  Returns decisions in scope, drift candidates, gaps, and divergences (Branch
  Problem Instance 4: non-superseded contradictory decisions on the same
  symbol). 3-5 suggested meeting questions. Heuristic-only in v0.4.6, no LLM.
  Skill at `skills/bicameral-brief/SKILL.md` enforces "divergences surface
  BEFORE decisions" rule.
- **`bicameral.reset(confirm=false)`** — Nuke-and-replay recovery path for a
  polluted ledger. Dry run by default, returns replay plan from
  `source_cursor` rows. `confirm=true` wipes every bicameral row scoped to
  the current repo via graph traversal (multi-repo isolation preserved).
  Skill at `skills/bicameral-reset/SKILL.md` enforces two-call dry-run pattern.
- **FC-2 multi-region grounding** — `_ground_single` in
  `adapters/code_locator.py` seeds the graph channel in
  `search_code(query, symbol_ids=...)` with fuzzy-validated symbol IDs,
  activating the RRF fusion layer (`code_locator/fusion/rrf.py`) that was
  previously built but unwired. Multi-file features (React component + hook
  + supabase function + migration) now ground to multiple real implementation
  files instead of collapsing to a single BM25 tiebreak winner. Pure wiring
  fix — no new infrastructure.

### Fixed

- **FC-1 BM25 degeneracy guard** — `RealCodeLocatorAdapter.ground_mappings`
  refuses to ground descriptions whose tokens reduce to fewer than 2 entries
  in the corpus vocabulary. Closes the spurious-anchor path where
  open-question intents ("GitHub Discussions vs Slack") collapsed to the
  densest-term-frequency file. New `Bm25sClient.count_corpus_tokens()` helper.
- **Silent branch pollution — Bug 1 (F1, `link_commit` write site)** —
  `ingest_commit` refuses baseline writes when the current branch name
  (via `git rev-parse --abbrev-ref HEAD`) doesn't match the authoritative
  ref. Drift is reported in memory but stored hashes, sync cursor, and intent
  status are not mutated. Branch-name comparison survives normal commits
  advancing main.
- **Silent branch pollution — Bug 3 (F1a, `ingest_payload` write site)** —
  `ingest_payload` now stamps baseline hashes against `ctx.authoritative_sha`
  instead of current HEAD. Fixes the day-1 poisoning vector where ingesting
  from a feature branch birthed a polluted ledger on a fresh install. A
  warning log line fires at ingest time when the user is on a non-authoritative
  ref.
- **Authoritative-ref auto-detection** — New helpers `detect_authoritative_ref`
  and `resolve_ref_sha` in `code_locator_runtime.py`. Resolution order:
  `BICAMERAL_AUTHORITATIVE_REF` env var → `git symbolic-ref refs/remotes/origin/HEAD`
  → fallback `"main"`. Stored on `BicameralContext` as `authoritative_ref` +
  `authoritative_sha` fields.

### Added — tests (+49 cases)

- `tests/test_fc1_bm25_degeneracy.py` — 11 cases: helper unit tests,
  5 fixture-driven degenerate queries, ground_mappings integration
- `tests/test_fc2_multi_region_grounding.py` — 4 cases: graph channel
  activation, multi-file emission, `max_symbols` cap, BM25-only fallback
- `tests/test_phase2_brief.py` — 17 cases: conflict heuristics, divergence
  detector, gap extractor, question generator, end-to-end with seeded ledger
- `tests/test_reset.py` — 4 cases: dry-run plan, actual wipe, multi-repo
  isolation, replay plan fidelity
- `tests/test_authoritative_ref.py` — 5 cases: env override, origin/HEAD
  detection, main fallback, resolve_ref_sha happy + missing
- `tests/test_pollution_bug.py` — 2 end-to-end cases with branched tmp git
  repo: ingest on branch stamps main-anchored hashes, link_commit on
  branch leaves stored baselines untouched

### Migration

No manual action required. `v0.4.5 → v0.4.6` is a handler + skill layer
release. No schema changes.

- Users on multi-branch workflows: set `BICAMERAL_AUTHORITATIVE_REF=<branch>`
  to override detection if `git symbolic-ref refs/remotes/origin/HEAD`
  doesn't return the expected branch (shallow clones, single-branch repos).
- Users whose v0.4.5 ledger got polluted by ingesting from a feature branch:
  run `bicameral.reset confirm=true` to wipe and replay from scratch.

### Known issues — will be fixed in v0.4.7

- **FC-3: Vocab cache cross-contamination + stale purpose field.** The
  vocab cache uses SurrealDB's `@0@` BM25 operator to match incoming
  descriptions against stored `query_text`, then reuses the cached symbols
  array without a similarity threshold. Two unrelated intents sharing
  incidental tokens can cross-match, and `_validate_cached_regions`
  preserves the cached region's `purpose` field (= the original intent's
  description), cross-wiring intents visually. Witnessed 2026-04-14 on
  Accountable: a Stripe payment-link fallback decision inherited 8 bogus
  regions from an earlier weekly-bulletin ingest. **Workaround:**
  `bicameral.reset confirm=true` clears vocab cache and re-ingest fresh.
  Fix planned for v0.4.7 — similarity gate on cache reuse + `purpose`
  field rewrite on hit.

### Rollback

Each feature is independently revertable via env var:
- `BICAMERAL_AUTHORITATIVE_REF=` (empty) → pollution guard disabled
- Removing `skills/bicameral-brief/` → brief skill unloaded

---

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
