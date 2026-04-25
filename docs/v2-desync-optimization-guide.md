# V2 Desync Optimization — Implementation Guide

**Status**: Planning artifact. No V2 code yet. V1 has shipped on this branch.
**Audience**: Any engineer or agent picking up V2 implementation.
**Self-contained**: This doc replaces `docs/desync-optimization.md` (V2 design with 9 Codex review passes) and `docs/desync-optimization-v1-plan.md` (V1 plan with pass-12 fixes folded in). Read this and you have the full picture.
**Date written**: 2026-04-25
**Branch**: `desync-optimization-v1` (V1 commits 3b4d0bb…8e226c5)
**Owner (V1)**: Silong
**Owner (V2)**: TBD — strongly recommend involving Jin (CODEOWNERS approval required, plus this work needs project-judgment review the adversarial Codex passes can't provide)

---

## Table of Contents

1. [Quick start](#1-quick-start)
2. [Background — what "desync" means here](#2-background)
3. [V1 outcomes — what's already shipped](#3-v1-outcomes)
4. [V2 scope — the gap and the goal](#4-v2-scope)
5. [Architecture target](#5-architecture-target)
6. [Implementation plan — phased, with hard dependencies](#6-implementation-plan)
7. [Constraints catalog — synthesized from 12 Codex review passes](#7-constraints-catalog)
8. [Open questions for human judgment](#8-open-questions)
9. [Acceptance criteria for V2](#9-acceptance-criteria)
10. [References](#10-references)

---

## 1. Quick start

V2 is the **destructive-path overhaul** of bicameral-mcp's drift-detection system. V1 (already on this branch) shipped measurement infrastructure, read-path advisory hints, a 13-scenario regression matrix, and a single safety fence around `bind` — without touching destructive write paths. **V2 ships the actual semantic drift detection, atomic rebind, reversible verdicts, and per-binding baseline ownership.**

If you're picking this up cold:

- **Read §3 first** to understand the V1 baseline you're building on (what works, what's deliberately deferred, what bug fixes were incidental).
- **Read §7 second**. Every entry there came from a Codex review pass that found a real bug in an earlier draft — those constraints are the difference between V2 shipping safely and V2 introducing data-corruption regressions.
- **Read §6 third**. The phase order is a real DAG with hard dependencies; don't deviate.
- **Read §8 last** before starting code. Several decisions remain open and benefit from human judgment, not adversarial review.

**Effort sense**: 7–10 engineer-weeks sequential, single owner. The phases don't parallelize cleanly because each one's correctness depends on the prior one's invariants.

---

## 2. Background

### 2.1 What "desync" means in this project

bicameral-mcp tracks three independently-evolving timelines:

| Timeline | Where it lives | Updates when |
|---|---|---|
| **Spec** | SurrealDB ledger (decisions, append-only) | User ingests a decision |
| **Index** | SQLite symbol DB | HEAD changes (rebuilt on mismatch) |
| **Code** | Git working tree / refs | Dev commits |

Every edge case where the `decision → symbol → code_region` graph becomes stale, missing, or wrong is a "desync scenario." The canonical reference is the Notion page **"The Auto-Grounding Problem: Keeping Decisions Linked to Code"** (Notion ID `3332a51619c4813caccec86c36d9bf98`). It catalogs **13 numbered scenarios** with severity tiers.

Supporting Notion docs:
- **"The Branch Problem"** (`3302a51619c48146b48dc675914beb6f`) — why content-hash anchoring beats SHA-anchoring; the content-hash is the stateless bridge between the spec lane and the code lane.
- **"CI Workflow Fixes — MCP Regression Pipeline (Apr 8)"** (`33c2a51619c48134ba8dc8bfaeb880dd`) — documents how scenarios #1 and #6 were false-negatives in tests because they bypassed `handle_ingest()` and called `ledger.ingest_payload()` directly. **Lesson: tests must route through the real handler layer.**

### 2.2 The 13-scenario catalog

Reproduced from Notion (severity tiers as of 2026-04-01, updated through V1):

| # | Scenario | Severity | V1 status |
|---|---|---|---|
| 1 | New decision ingested, matching code exists | was P0 | ✅ caller-LLM bind flow |
| 2 | Code changed after decision was grounded | working | ✅ pending + `pending_compliance_check` |
| 3 | Code deleted after decision was grounded | working | ✅ symbol_disappeared |
| 4 | Symbol renamed (refactor) | P1 | ✅ symbol_disappeared with `original_lines` (V1 D1) |
| 5 | Symbol moved to different file | P1 | ✅ symbol_disappeared |
| 6 | Code index rebuilt with new symbols | was P0 | ✅ caller binds explicitly |
| 7 | Cold start: no code index | working | ✅ stays ungrounded |
| 8 | Drifted intent → recoverable via re-ground | P1 (V2) | ⏸ XFAIL (atomic rebind = V2 D2) |
| 9 | Intent description supersession | P2 | ✅ re-ingest succeeds |
| 10 | Multiple intents map to same symbol | working | ✅ both surface |
| 11 | BM25 false-positive grounding | post-v0.6.0: N/A | ✅ caller-LLM-driven |
| 12 | Code region line numbers shift (insertion above) | working | ✅ `resolve_symbol_lines` self-heals |
| 13 | `[Open Question]` prefix → gap classification | v0.5.x | ✅ ingested as gap |

**Current scorecard: 12 PASS / 1 XFAIL.** Scenario 8 flips to PASS the moment V2's `bicameral_rebind` lands — the test is `@pytest.mark.xfail(strict=True)` so a `xpassed` result will be a CI-visible signal that V2 has implemented the missing piece.

### 2.3 Scorecard trajectory

| Date | Scorecard | Source |
|---|---|---|
| 2026-04-01 | 10/13 (77%) | Notion auto-grounding doc, original analysis |
| 2026-04-08 | 12/13 (92%) | CI Workflow Fixes — PR #84 routed tests through real handler layer |
| 2026-04-23 (v0.6.1) | G1 + G3 closed via sync_middleware | `CHANGELOG.md` |
| 2026-04-23 (v0.6.0) | server-side BM25 auto-grounding **removed** (–2317 LOC) | architectural shift to caller-LLM-driven retrieval |
| 2026-04-23 (v0.6.4) | `search_code` deleted | "caller-LLM owns all code retrieval" |
| 2026-04-25 (V1 done) | 12/13 PASS + 1 XFAIL on V2 | this branch |

---

## 3. V1 outcomes

V1 commits on this branch (`origin/main` is `a5aface`):

```
8e226c5 docs: tick V1 desync optimization across CHANGELOG / TODO / PLAN
a04e54b fix(link_commit): split verification_instruction so relocation cases don't get bind CTA
89f8076 feat: desync optimization V1 F1 — canonical 13-scenario regression matrix
54081e6 feat: desync optimization V1 D1 — original_lines on symbol-disappeared payload
401babc feat: desync optimization V1 Phase B — read-path cosmetic-change advisory
3b4d0bb feat: desync optimization V1 Phase A — measurement + light sync hardening
```

### 3.1 What V1 delivered

V1 introduces **zero new mutating capabilities**. Every change is one of: read-only measurement, additive contract field, pure function, test coverage, or a surgical bug fix to an already-shipped path.

| ID | Deliverable | Files |
|---|---|---|
| **A1** | Drift benchmark harness — seeds 100 decisions × 25 files, times search/drift/link_commit, writes JSON artifact, marked `@pytest.mark.bench` | `tests/bench_drift.py` |
| **A2-light** | Per-repo `asyncio.Lock` for `handle_bind`. In-process serialization only — does NOT protect `resolve_compliance` or cross-process writers | `handlers/sync_middleware.py::repo_write_barrier`, `handlers/bind.py` |
| **A3** | `SyncMetrics` (`sync_catchup_ms` / `barrier_held_ms`) attached to Search/Preflight/History/Bind responses. Each handler times its own sync call locally so nested calls don't step on each other's metrics | `contracts.py::SyncMetrics`, four handlers |
| **B1** | Strict-whitelist tree-sitter cosmetic-change classifier. Returns True ONLY for inter-token whitespace differences. Variable renames, comment edits, docstring changes, trailing commas, import reorders all return False | `ledger/ast_diff.py` |
| **B2** | `DriftEntry.cosmetic_hint` advisory metadata. Read-path only — never mutates `content_hash`, never gates drift surfacing | `contracts.py::DriftEntry`, `handlers/detect_drift.py::_enrich_with_cosmetic_hints` |
| **D1** | `original_lines` on `symbol_disappeared` grounding checks so caller LLM can `git show <prev_ref>:<file_path>` to inspect the symbol's prior position | `ledger/adapter.py:412-420` |
| **D1 follow-up** | `_build_verification_instruction` split — relocation cases get an explicit "do NOT call bicameral.bind" warning instead of the v0.6.4 monolithic bind CTA | `handlers/link_commit.py::_build_verification_instruction` |
| **F1** | Canonical 13-scenario regression matrix routed through real handler layer. Self-contained tmp-repo fixture per test | `tests/test_desync_scenarios.py` |
| **Bug fix (incidental)** | `pending_grounding_checks` for ungrounded decisions emitted empty `decision_id` because consumer read `d.get("id", "")` from rows aliased to `decision_id`. Surfaced by F1 | `ledger/adapter.py:475` |

**Performance baseline (post-rebase, surrealdb 2.0.0, Apple Silicon):**

| handler | p50 | p95 | max |
|---|---|---|---|
| search_decisions | 9.2ms | 10.4ms | 11.0ms |
| detect_drift | 14.2ms | 15.5ms | 16.4ms |
| link_commit (warm) | 7.3ms | 8.0ms | 8.3ms |

All 50–185× under the V2 perf targets (`PLAN.md:83`: search < 2s, drift < 1s).

### 3.2 What V1 explicitly did NOT do

The recurring framing across Codex review passes was: "V1 is shippable while destructive paths exist." This is technically true and worth being explicit about. **V1 introduces zero new destructive paths.** Every mutating capability that V1 ships is either:

- already present in main pre-V1 (e.g. `resolve_compliance` hard-delete from v0.5.0; `bicameral.bind` from v0.6.0; the auto-chained `handle_judge_gaps` from `handlers/ingest.py`), OR
- a surgical bug fix to an already-shipped path (the `decision_id` empty-string fix in `ledger/adapter.py:475`).

Net destructive-surface change for V1: **zero (and arguably negative via D1's CTA removal).**

### 3.3 Practical user-facing impact of V1

V1 is roughly **20–30% of the user-facing value of "actual desync optimization."** It's foundation + safety fences. The things that change what someone *experiences* using bicameral are mostly V2:

- **`derive_status` still returns `pending` (not `drifted`)** for hash-divergent regions without a cached compliant verdict (`ledger/status.py:178-205`). The actual semantic "drifted" classification requires a caller-LLM verdict, which is V2's `bicameral_judge_drift`. So today, when a developer changes code, they get `pending`, not `drifted` — "we don't know yet" rather than a real verdict.
- **Rename recovery is informational only.** Caller can read `original_lines` from a `symbol_disappeared` payload but acting on it (calling `bicameral.bind`) creates duplicate-binding state. V1 actively warns them to wait for V2.
- **The destructive backdoor is still live.** `resolve_compliance` still hard-deletes `binds_to` edges on `not_relevant` verdicts; one bad async caller verdict can permanently remove a decision's only grounding edge with no recovery path.
- **Cross-decision baseline corruption is still possible.** When multiple decisions share a region, one decision's effects on shared state ripple to the others.

V1's value is operational confidence + one footgun closed + one race narrowed + foundation for V2. V2 is where "drifted" becomes a real claim, where rename recovery becomes safe, and where the destructive backdoor closes.

---

## 4. V2 scope

### 4.1 Capability gap (V1 → V2)

| # | Capability | Currently | V2 needs |
|---|---|---|---|
| 1 | Atomic multi-statement writes | `LedgerClient.execute_many` is sequential, no rollback. No transaction primitive in repo. | **A0**: SurrealQL `BEGIN/COMMIT TRANSACTION` blocks submitted as single `query()` calls. (Embedded SDK doesn't support `begin_transaction()` — verified empirically; see [SurrealDB Python SDK docs](https://surrealdb.com/docs/sdk/python/concepts/connecting-to-surrealdb).) |
| 2 | Existing destructive backdoor | `handlers/resolve_compliance.py:122` hard-deletes `binds_to` on `not_relevant`; `handlers/ingest.py:313-331` auto-chains into it via `handle_judge_gaps`. | Migrate to tombstone + full CAS **before** any new mutating tool ships. Codex pass-10 #1 — the **hard prerequisite**. |
| 3 | Per-binding baseline ownership | `code_region.content_hash` is shared across N decisions bound to the same region. One decision's verdict rewrites everyone's drift baseline. | **C0**: move `baseline_content_hash`, `baseline_commit_hash`, `binding_version` onto `binds_to` edges. `derive_status` rewritten per-binding. |
| 4 | Reversible verdict storage | `compliance_check` has `UNIQUE(decision_id, region_id, content_hash)` (`ledger/schema.py:163`). Contradicting later verdict overwrites the prior one — reversal physically impossible. | **C0**: append-only `compliance_verdict_history` table + `compliance_check` redefined as a current-state projection over the full 7-field CAS tuple. |
| 5 | Tombstone semantics on `binds_to` | No tombstone fields. Edge deletion is the only retirement mechanism. | **C0**: add `tombstoned_at`, `tombstone_reason`, `tombstone_verdict_id`. **C0a**: every `binds_to` traversal site filtered via shared `binds_to_active_filter()`. |
| 6 | Full-CAS cache key | `idx_cc_cache_key UNIQUE(decision_id, region_id, content_hash)` — replays old verdicts across reverts/branches/moves. | **C0**: replace with 7-field `(decision_id, region_id, content_hash, commit_hash, file_path, binding_version, tombstone_verdict_id)`. **Same** tuple referenced verbatim in schema + migration + cache lookup + write upsert. |
| 7 | Commit-time sync barrier | A2-light (V1) only catches in-process races. HEAD can change between sync and commit; working-tree edits don't move HEAD. | **A2a**: per-handler `SyncToken{head_sha, ...}` re-checked against `git rev-parse HEAD` immediately before COMMIT, plus per-region `RegionFingerprint{file_path, content_hash, binding_version, mtime, size}` re-verified at commit time. |
| 8 | LLM compliance verdict tool | `derive_status` returns `pending` (not `drifted`) when no verdict cached — V1 scenario 2 documents this. | **C2**: `bicameral_judge_drift` (caller-LLM) + `record_compliance_verdict` with five-field CAS token (code identity + binding state). Stale verdicts go to history with `stale_reason`, never mutate live state. |
| 9 | Cache-aware drift surfacing | `detect_drift` doesn't emit `pending_compliance_checks`. | **C3**: emit `pending_compliance_checks` for every hash-divergent region; cosmetic_hint is metadata only, never a gate. |
| 10 | Baseline advancement | `code_region.content_hash` updates only via `link_commit` sweep; no caller-driven advancement. | **B3**: `bicameral_advance_baseline(decision_id, region_id, cas_token, verdict_id)` — only accepts a fresh L3 `compliant` verdict matching all five CAS components. Writes to a single `binds_to` edge; never touches shared region state. No `ast_cosmetic` reason. |
| 11 | Atomic rebind | Rename → `symbol_disappeared` payload (V1 D1). Manual `bicameral.bind` would create duplicate-binding state under N:N `binds_to`. | **D2**: `bicameral_rebind` with `expected_old_binding_version` + `expected_old_tombstone_verdict_id` CAS, **two-phase** semantics (Codex pass-11 #2): create new as pending → fresh L3 verdict on new target → tombstone old. Closes scenario 8. |
| 12 | Doctor skill rendering | `.claude/skills/bicameral-doctor/SKILL.md` exists (211 lines) but contains zero `pending_grounding_checks` / `cosmetic_hint` / verdict-related prose. | Once V2 has safe atomic rebind, render the new payloads as advisory context with the (now-safe) bind flow for relocation cases. |

### 4.2 V2 product targets

After V2 ships, the user-visible improvements:

- **"drifted" becomes a real claim.** A drifted status indicates a caller-LLM has reviewed the change and confirmed it diverges from the decision — not just "bytes are different and we don't know yet."
- **Rename/move recovery is safe.** `bicameral_rebind` retires the old edge and creates the new one in a single transaction with full CAS protection.
- **`resolve_compliance` no longer corrupts state on bad verdicts.** Tombstone + CAS means a stale `not_relevant` verdict is rejected (or recorded as stale-history-only) instead of silently deleting the only grounding edge.
- **Cross-decision baseline isolation.** Each decision-binding has its own baseline; one decision's `advance_baseline` doesn't ripple to peer decisions on the same region.
- **Reversible verdicts with full audit history.** Operators can see every verdict ever issued for a region, and a contradicting later verdict (e.g. operator restores a tombstone) is recorded in history rather than overwriting.
- **Scenario 8 flips from xfail to pass** — the canonical "drifted intent recoverable via re-ground" scenario actually works end-to-end.

---

## 5. Architecture target

### 5.1 Layer 1 / Layer 2 / Layer 3 model

Drift detection has three layers, only Layer 1 is wired today:

| Layer | Mechanism | Catches | V1 status | V2 status |
|---|---|---|---|---|
| **L1** | Content-hash comparison (`HashDriftAnalyzer`, `ledger/drift.py`) — syntactic identity | Any byte-level change | ✅ Shipped | unchanged |
| **L2** | AST pre-filter (tree-sitter strict whitelist via `ledger/ast_diff.is_cosmetic_change`) | Whitespace, blank lines | ✅ Shipped (V1 B1/B2) — **advisory only**, never gates L3 | unchanged; the `cosmetic_hint` field becomes input to L3 prompt rendering |
| **L3** | LLM compliance check (`claude-haiku-4-5` or similar) — "does code still satisfy intent?" | Semantic compliance vs noise | ❌ Not built | **V2 C2**: `bicameral_judge_drift` (caller-LLM) + `record_compliance_verdict` with 5-field CAS |

L1 alone produces noise on every rename/format change. L2 narrows the noise but cannot prove semantic equivalence. L3 is the only judge that can — and the entire V2 story is about making L3 verdicts authoritative, reversible, and auditable.

### 5.2 Per-binding state ownership (the pass-8 redesign)

**The bug**: V1 keeps baseline state on shared `code_region.content_hash`. But `binds_to` is N:N — multiple decisions can bind to the same `code_region`. With baseline state on the shared region, one decision's `advance_baseline` would silently rewrite the drift baseline for every other decision bound to the same region; a region-version bump would invalidate other decisions' caches without authorization. **Cross-decision correctness bug.**

**The fix**: move baseline ownership off shared `code_region` and onto the per-binding `binds_to` edge.

```sql
-- V2 schema additions to binds_to
DEFINE FIELD baseline_content_hash ON binds_to TYPE string;
DEFINE FIELD baseline_commit_hash  ON binds_to TYPE string DEFAULT '';
DEFINE FIELD binding_version       ON binds_to TYPE int DEFAULT 1;

-- Tombstone fields (separate concern, but same edge)
DEFINE FIELD tombstoned_at         ON binds_to TYPE datetime | NONE;
DEFINE FIELD tombstone_reason      ON binds_to TYPE string DEFAULT '';
DEFINE FIELD tombstone_verdict_id  ON binds_to TYPE string DEFAULT '';
```

`code_region` keeps **only location data** (`file_path`, `symbol_name`, `start_line_snapshot`, `end_line_snapshot`). Line snapshots are advisory hints, not source of truth — `derive_status` always re-resolves the symbol via `resolve_symbol_lines(file_path, symbol_name)` (`ledger/status.py:21-89`) before hashing. **Region identity is the symbol, not the line range.**

**`derive_status` rewritten** to compare live hash against `binds_to.baseline_content_hash` per-binding instead of against shared `code_region.content_hash`.

### 5.3 Full CAS contract — five-field token

Every mutating tool that takes a caller-LLM verdict requires a `cas_token`:

```python
{
    "expected_content_hash": str,    # bytes the caller judged
    "expected_commit_hash": str,     # commit at judgment time
    "expected_file_path": str,       # path at judgment time
    "expected_binding_version": int, # binds_to edge version
    "expected_tombstone_verdict_id": str,  # '' for live edges
}
```

`record_compliance_verdict`, `bicameral_advance_baseline`, and `bicameral_rebind` all CAS-check **all five fields** before any mutation. Mismatch → record verdict in `compliance_verdict_history` with `stale=true, stale_reason='<specific_field>_mismatch'` and **do not** mutate live state. Each component catches a distinct desync class:

- `content_hash` mismatch → bytes changed under the caller
- `commit_hash` mismatch → HEAD moved (branch switch, revert, new commit)
- `file_path` mismatch → region was relocated since judgment
- `binding_version` mismatch → this binding was rebaselined or replaced
- `tombstone_verdict_id` mismatch → operator restored / re-tombstoned the binding

### 5.4 Tombstone semantics on `binds_to`

`not_relevant` verdicts (from `bicameral_judge_drift` or the existing `resolve_compliance` flow) **do not hard-delete** the edge. Instead:

- Set `tombstoned_at = time::now()`, `tombstone_reason = '<source>:<reason>'`, `tombstone_verdict_id = <history row id>`.
- Edge is excluded from drift / status walks via shared `binds_to_active_filter()` helper used by every traversal site.
- `bicameral_restore_binding(decision_id, region_id, expected_tombstone_verdict_id)` lifts the tombstone — auditable via a synthetic history row.
- Hard-delete is **not** part of V2. A separate scheduled GC handler can purge tombstones older than N days with no contradicting verdict (deferred to V3 or operator config).

### 5.5 Append-only `compliance_verdict_history`

```sql
DEFINE TABLE compliance_verdict_history SCHEMAFULL;
DEFINE FIELD decision_id ON compliance_verdict_history TYPE string;
DEFINE FIELD region_id ON compliance_verdict_history TYPE string;
DEFINE FIELD verdict ON compliance_verdict_history TYPE string;  -- compliant | drifted | not_relevant | restored
DEFINE FIELD confidence ON compliance_verdict_history TYPE string;
DEFINE FIELD explanation ON compliance_verdict_history TYPE string DEFAULT '';
DEFINE FIELD agent_id ON compliance_verdict_history TYPE string DEFAULT '';
DEFINE FIELD stale ON compliance_verdict_history TYPE bool DEFAULT false;
DEFINE FIELD stale_reason ON compliance_verdict_history TYPE string DEFAULT '';
DEFINE FIELD recorded_at ON compliance_verdict_history TYPE datetime DEFAULT time::now();
-- Full CAS captured on every row
DEFINE FIELD expected_content_hash ON compliance_verdict_history TYPE string;
DEFINE FIELD expected_commit_hash ON compliance_verdict_history TYPE string;
DEFINE FIELD expected_file_path ON compliance_verdict_history TYPE string;
DEFINE FIELD expected_binding_version ON compliance_verdict_history TYPE int | NONE;
DEFINE FIELD expected_tombstone_verdict_id ON compliance_verdict_history TYPE string DEFAULT '';
DEFINE FIELD actual_content_hash ON compliance_verdict_history TYPE string DEFAULT '';
DEFINE FIELD actual_commit_hash ON compliance_verdict_history TYPE string DEFAULT '';
DEFINE FIELD actual_file_path ON compliance_verdict_history TYPE string DEFAULT '';
DEFINE FIELD actual_binding_version ON compliance_verdict_history TYPE int | NONE;
DEFINE FIELD actual_tombstone_verdict_id ON compliance_verdict_history TYPE string DEFAULT '';
-- No uniqueness — same code shape can hold an unbounded sequence of verdicts
DEFINE INDEX idx_cvh_lookup ON compliance_verdict_history
    FIELDS decision_id, region_id, expected_content_hash, expected_commit_hash, recorded_at DESC;
DEFINE INDEX idx_cvh_audit ON compliance_verdict_history FIELDS decision_id;
DEFINE INDEX idx_cvh_stale ON compliance_verdict_history FIELDS stale;
```

`compliance_check` is **redefined as a current-state projection** over the full 7-field CAS tuple, kept in sync from `compliance_verdict_history`:

```sql
DEFINE INDEX idx_cc_cache_key ON compliance_check
    FIELDS decision_id, region_id, content_hash, commit_hash, file_path, binding_version, tombstone_verdict_id
    UNIQUE;
```

**Same content hash at a different commit / path / binding_version / tombstone state produces a different projection row, not an overwrite.** This is the single source of truth — schema, migration, cache lookup, and write upsert all key on this exact tuple.

**Migration strategy** (Codex pass-6 #3): legacy `compliance_check` rows lack the new CAS columns. **Do not backfill from current state** — that fabricates history. Instead:

1. Read every legacy `compliance_check` row.
2. Insert each into `compliance_verdict_history` with `stale=true, stale_reason='legacy_pre_v6_no_cas_metadata', expected_binding_version=NULL, expected_tombstone_verdict_id=NULL, expected_file_path=NULL`. The verdict text is preserved for audit.
3. Drop and recreate `compliance_check` empty with the new index.
4. Cache lookups against the empty projection always miss → every previously-cached region gets fresh L3 on its next `detect_drift` call. Cost is bounded; benefit is no false cache hits.

### 5.6 Two-phase atomic rebind (pass-11 + pass-13 fixes)

Pass-11 finding: a single-transaction "create new + tombstone old" rebind retires the authoritative binding before the new target has been semantically proven. A wrong candidate selection silently reattaches the decision to unrelated code. → fixed by splitting rebind into two phases (this section).

**Pass-13 finding**: a naive two-phase rebind that only carries the *new* binding's CAS token in phase 2 still has a misattachment bug. If the caller does multiple phase-1 attempts on the same `old_region_id` (candidate A, then candidate B), and a stale phase-2 compliant verdict for candidate A arrives after the caller has moved on to B, the server would tombstone the old edge based on a verdict the caller no longer endorses. → fixed by binding phase 2 to a specific `rebind_attempt_id` and enforcing single-pending-rebind-per-old-binding (this section, below).

#### Schema additions for D2

```sql
-- On binds_to:
DEFINE FIELD pending_rebind_attempt_id ON binds_to TYPE string DEFAULT '';
DEFINE FIELD rebind_attempt_id         ON binds_to TYPE string DEFAULT '';
DEFINE FIELD pending_verification      ON binds_to TYPE bool DEFAULT false;

-- New table: rebind_audit
DEFINE TABLE rebind_audit SCHEMAFULL;
DEFINE FIELD attempt_id          ON rebind_audit TYPE string;     -- UUID, immutable, primary phase 2 token
DEFINE FIELD decision_id         ON rebind_audit TYPE string;
DEFINE FIELD old_region_id       ON rebind_audit TYPE string;
DEFINE FIELD new_region_id       ON rebind_audit TYPE string;
DEFINE FIELD old_binding_version_at_attempt   ON rebind_audit TYPE int;  -- snapshot for phase-2 CAS
DEFINE FIELD old_tombstone_verdict_id_at_attempt ON rebind_audit TYPE string;
DEFINE FIELD reason              ON rebind_audit TYPE string;
DEFINE FIELD agent_id            ON rebind_audit TYPE string;
DEFINE FIELD recorded_at         ON rebind_audit TYPE datetime DEFAULT time::now();
DEFINE FIELD expires_at          ON rebind_audit TYPE datetime;  -- recorded_at + REBIND_LEASE_TTL
DEFINE FIELD outcome             ON rebind_audit TYPE string DEFAULT 'pending';
    -- pending | committed | superseded | abandoned | abandoned_by_expiry
DEFINE INDEX idx_rebind_attempt  ON rebind_audit FIELDS attempt_id UNIQUE;
DEFINE INDEX idx_rebind_pending  ON rebind_audit FIELDS old_region_id, outcome;
DEFINE INDEX idx_rebind_expiry   ON rebind_audit FIELDS outcome, expires_at;
```

`REBIND_LEASE_TTL` is configurable via `BICAMERAL_REBIND_LEASE_SECONDS` (default 86400 — 24 hours). Long enough that a careful caller-LLM can take its time on the L3 review; short enough that a crashed caller doesn't wedge a binding indefinitely.

#### Protocol

```
Phase 1 — bicameral_rebind(decision_id, old_region_id, new_location | new_region_id,
                           reason, agent_id,
                           expected_old_binding_version,
                           expected_old_tombstone_verdict_id,
                           force_supersede: bool = false)
  Under repo_write_barrier + A2a + atomic transaction (A0):
    1. Re-read old binding state. CAS-check expected_old_*. Mismatch → abort.
    2. Lease-expiry sweep (cheap, runs every phase 1):
       - Read existing pending attempt: rebind_audit row where
         attempt_id == old_binding.pending_rebind_attempt_id AND outcome == 'pending'.
       - If row exists AND row.expires_at < now(): in this same transaction
         abandon it (set outcome='abandoned_by_expiry', tombstone the orphan
         new binding with tombstone_reason='rebind:expired', clear
         old_binding.pending_rebind_attempt_id). Treat the lock as released.
    3. Lock check:
       - If old_binding.pending_rebind_attempt_id == '' (post-sweep): proceed.
       - Else if force_supersede == true: abandon the existing attempt
         (outcome='superseded', tombstone orphan new binding with
         tombstone_reason='rebind:superseded'); proceed.
       - Else: abort with rebind_already_pending and return the existing
         attempt_id + its expires_at so the caller can either wait, retry
         with force_supersede=true, or call bicameral_abandon_rebind.
    4. Generate a fresh attempt_id (UUID).
    5. Insert rebind_audit row with outcome='pending', snapshot
       old_binding_version_at_attempt and old_tombstone_verdict_id_at_attempt,
       set expires_at = now() + REBIND_LEASE_TTL.
    6. Bump old binding's binding_version (invalidates in-flight verdicts on
       the old edge). Set old_binding.pending_rebind_attempt_id = attempt_id.
    7. Resolve new code_region. If the new binding edge already exists from
       a prior tombstoned rebind: bump its binding_version and clear its
       tombstone fields. Otherwise create binds_to(decision → new_region)
       with binding_version=1.
    8. Mark the new binding pending_verification=true,
       rebind_attempt_id=attempt_id; initialize baseline_* from a live
       region read.
    9. Return (new binding's full 5-field CAS token, attempt_id, audit_id,
       expires_at).

Phase 2 — record_compliance_verdict(decision_id, region_id=new_region_id,
                                    cas_token, verdict, ...)
  Under repo_write_barrier + A2a + atomic transaction (A0):
    1. Re-read NEW binding state. CAS-check the 5-field cas_token.
       Mismatch → record stale-history-only, no mutation.
    2. If new binding has pending_verification=true (it's part of a rebind):
       a. Look up rebind_audit by new_binding.rebind_attempt_id.
       b. Lease check: if rebind_audit.outcome != 'pending' OR
          rebind_audit.expires_at < now(): record verdict in
          compliance_verdict_history with stale=true,
          stale_reason='rebind_attempt_expired' (or '_superseded' / '_abandoned'
          per outcome). Do NOT tombstone old binding. Do NOT touch projection.
       c. Re-read OLD binding state. Verify
          old_binding.pending_rebind_attempt_id == new_binding.rebind_attempt_id.
          Mismatch → record verdict with stale=true,
          stale_reason='rebind_attempt_superseded'.
          Do NOT tombstone old binding. Do NOT touch projection.
       d. Verify old_binding.binding_version ==
          rebind_audit.old_binding_version_at_attempt AND
          old_binding.tombstone_verdict_id ==
          rebind_audit.old_tombstone_verdict_id_at_attempt.
          Mismatch → same stale-history-only path.
       e. If verdict == 'compliant': in the SAME transaction, set
          new_binding.pending_verification=false, clear new_binding.rebind_attempt_id,
          tombstone old_binding (set tombstoned_at, tombstone_reason='rebind:<reason>',
          tombstone_verdict_id=<verdict_history_id>), clear
          old_binding.pending_rebind_attempt_id, set rebind_audit.outcome='committed'.
       f. If verdict in {'drifted','not_relevant'}: new binding stays
          pending_verification=true; old binding stays live with its lock.
          Caller may retry phase-1 with a different candidate via either
          (i) bicameral_abandon_rebind(attempt_id, ...), or
          (ii) a fresh bicameral_rebind with force_supersede=true, or
          (iii) waiting for the lease to expire (the next phase-1 attempt
          will sweep it). All three paths converge on outcome='abandoned'
          / 'superseded' / 'abandoned_by_expiry' on the audit row.
    3. Then proceed with the standard verdict-write algorithm in §5.5.

bicameral_abandon_rebind(attempt_id, expected_old_binding_version,
                        expected_old_tombstone_verdict_id) — caller-driven
  abandon. CAS-check the old binding under barrier. Set
  rebind_audit.outcome='abandoned', clear old_binding.pending_rebind_attempt_id,
  tombstone the orphan new binding with tombstone_reason='rebind:abandoned'.
```

This means:

- **`bicameral_rebind` alone never retires the old edge.** Old-edge tombstoning is gated on a fresh `compliant` verdict whose `rebind_attempt_id` matches the lock currently held on the old binding.
- **At most one pending rebind per old binding.** Subsequent phase-1 attempts on the same `old_region_id` return `rebind_already_pending` until the prior attempt is committed, abandoned, or superseded — except when the caller passes `force_supersede=true` to abandon-and-replace atomically.
- **Stale phase-2 verdicts are rejected.** A compliant verdict on an abandoned/superseded/expired attempt fails the lease check or the `pending_rebind_attempt_id == rebind_attempt_id` check and is recorded with the appropriate `stale_reason`. The old binding is NOT tombstoned.
- **No deadlock under client crash** (pass-14 #2): every pending attempt has an `expires_at` deadline (default 24h via `BICAMERAL_REBIND_LEASE_SECONDS`). The next `bicameral_rebind` call against the same `old_region_id` runs an expiry sweep that atomically abandons stale leases before issuing a new attempt, so a crashed or abandoned caller cannot wedge a binding indefinitely. An optional background sweep (`bicameral.maintenance` or a cron) can also clear expired leases proactively, but is not required for liveness — the on-demand sweep guarantees forward progress.

### 5.7 Sync barrier with commit-time CAS (the pass-4 / pass-5 design)

V1 A2-light only catches in-process races on `bind`. V2 needs three complementary mechanisms, all required:

1. **Per-repo `asyncio.Lock`** (already shipped in V1 as `repo_write_barrier`) — in-process serialization. Wrap every code-shape mutator with this.
2. **Sync token CAS at commit time** — `require_ledger_synced(ctx)` returns `SyncToken{head_sha, sync_at, ledger_version}`. Every ledger write takes the token. Just before COMMIT, re-read `git rev-parse HEAD` and verify it equals `token.head_sha`. Mismatch → abort with `head_changed_mid_handler`. Catches out-of-process HEAD changes.
3. **Per-region CAS at commit time** — for handlers that read code shape (rebind, advance_baseline, record_compliance_verdict), snapshot `RegionFingerprint{region_id, file_path, symbol_name, resolved_start_line, resolved_end_line, resolved_content_hash, binding_version, file_mtime, file_size}` at sync time, re-verify at commit time. Catches working-tree races (uncommitted edits) and file-move races where HEAD didn't move.

### 5.8 Spec writes vs code-shape writes (pass-6 #1)

**Two classes of mutators with different correctness requirements**:

- **Spec writes (append-only, do NOT gate on sync failure)**: `handlers/ingest.py`, `handlers/ratify.py`. These persist user *intent*. Today's `ingest.py:283,290` does write-first then best-effort `link_commit`. Preserve that. Gating ingest on git failure would lose decisions — a higher-cost failure mode than today's desync.
- **Code-shape writes (DO gate fail-closed)**: `handlers/bind.py`, `handlers/resolve_compliance.py`, plus the new `bicameral_rebind`, `bicameral_advance_baseline`, `record_compliance_verdict`. These mutate state derived from current code; stale views encode wrong facts.

`require_ledger_synced(ctx)` returns `SyncResult(ok, head_sha, error)` — does **not** swallow exceptions. Code-shape handlers abort on `ok=False` with a structured `degraded_sync` error. Spec handlers retain best-effort sync with a `sync_degraded` warning flag in the response.

---

## 6. Implementation plan

```
┌─ Phase 0 (Prereq) ──────────────────────────────────┐
│ 0a   Migrate resolve_compliance.py → tombstone+CAS  │  ← absolute prerequisite
│      (no new mutating tools until this is done)     │     (Codex pass-10 #1)
│ 0b   A0: Atomic SurrealQL block primitive            │
└─────────────────────┬───────────────────────────────┘
                      │
┌─ Phase 1 (Schema) ──▼───────────────────────────────┐
│ C0   v5→v6 migration: per-binding baseline,          │
│      tombstone fields, compliance_verdict_history,   │
│      full-CAS cache key                              │
│ C0a  Traversal filtering across all binds_to         │
│      consumers via binds_to_active_filter()          │
└─────────────────────┬───────────────────────────────┘
                      │
┌─ Phase 2 (Barrier) ─▼───────────────────────────────┐
│ A2a  SyncToken CAS + RegionFingerprint at commit     │
│      Apply to every code-shape mutator               │
└─────────────────────┬───────────────────────────────┘
                      │
┌─ Phase 3 (Reads) ───▼───────────────────────────────┐
│ C1   Cache lookup with full CAS                      │
│ C3   pending_compliance_checks from detect_drift     │
└─────────────────────┬───────────────────────────────┘
                      │
┌─ Phase 4 (Writes) ──▼───────────────────────────────┐
│ C2   bicameral_judge_drift + record_compliance       │
│      (5-field CAS, stale-verdict history)            │
│ B3   bicameral_advance_baseline                      │
│      (only L3 compliant verdicts; per-binding;       │
│       no ast_cosmetic)                               │
│ D2   bicameral_rebind (two-phase, pass-11 fix)       │
│      (old-binding CAS + L3 verdict on new target     │
│       before old is tombstoned)                      │
└─────────────────────┬───────────────────────────────┘
                      │
┌─ Phase 5 (Polish) ──▼───────────────────────────────┐
│ .claude/skills/bicameral-doctor/SKILL.md rendering   │
│ Re-run Codex review (target: pass-13 ships clean)    │
│ Convert scenario 8 from xfail → expected pass        │
└─────────────────────────────────────────────────────┘
```

### Phase 0 — Hard prerequisites (1–2 weeks)

**0a. Migrate `handlers/resolve_compliance.py` from hard-delete to tombstone**

Files: `handlers/resolve_compliance.py`, `ledger/queries.py` (or `ledger/adapter.py`), tests.

Current behavior: `not_relevant` verdict → `delete_binds_to_edge(client, decision_id, region_id)` (line 122). One bad async caller verdict permanently removes the only grounding edge with no recovery path.

Target behavior: `not_relevant` verdict → set tombstone fields on the edge with `tombstone_reason = 'judge_gaps:not_relevant'`. Surface a `bicameral_restore_binding` tool to lift tombstones.

This is the single highest-leverage move. It closes the highest-impact destructive write path before any new tool inherits the same backdoor. **Do this even before A0** — single `UPDATE` to set tombstone fields atomically replaces the `DELETE`, no transaction primitive required yet.

Tests: extend `tests/test_resolve_compliance.py` to assert tombstone state instead of edge deletion. Add `tests/test_restore_binding.py` for the new tool. `tests/test_desync_scenarios.py` still passes because it doesn't exercise this path.

**0b. A0 — Atomic SurrealQL block primitive**

File: `ledger/client.py`.

Background: `LedgerClient.execute_many` (lines 117-122) is sequential. Embedded SurrealKV doesn't support `begin_transaction()` via the Python SDK ([source](https://surrealdb.com/docs/sdk/python/concepts/connecting-to-surrealdb)).

**The chosen mechanism (pass-14 #1 — pick is committed in this guide; do not defer):**

Add `LedgerClient.transaction()` — async context manager that submits a `BEGIN TRANSACTION; <stmt1>; <stmt2>; ...; COMMIT TRANSACTION;` SurrealQL block as a single `query()` call. Parse the per-statement results; if any statement returns `status: ERR`, append `CANCEL TRANSACTION` to the block (or rely on SurrealDB's automatic cancellation on per-statement error) and raise `LedgerError` carrying the failed statement and index.

```python
# ledger/client.py target shape
@asynccontextmanager
async def transaction(self) -> AsyncIterator["TransactionBuffer"]:
    """Buffer SurrealQL statements; submit them as one atomic block on exit."""
    buf = TransactionBuffer()
    yield buf
    if not buf.statements:
        return
    block = "BEGIN TRANSACTION;\n" + ";\n".join(buf.statements) + ";\nCOMMIT TRANSACTION;"
    result = await self._db.query(block, buf.vars)
    # SurrealDB returns one result element per statement; on per-statement error,
    # the COMMIT auto-cancels and earlier statements roll back.
    for i, stmt in enumerate(result):
        if isinstance(stmt, str):  # error string from SurrealDB
            raise LedgerError(f"transaction statement {i} failed: {stmt[:300]}")
```

**Why this and not the `LET`-chain alternative**: `BEGIN/COMMIT TRANSACTION` is the documented atomicity primitive in SurrealQL ([SurrealQL Transactions](https://surrealdb.com/docs/surrealql/transactions)) and matches the semantic shape every V2 mutation needs (history insert + binds_to update + projection upsert). Single-statement `LET`-chains can express the same writes but constrain query shape and cannot use procedural control flow if a future mutation needs it. The chosen mechanism is more general; the gate test below verifies it actually works in our deployment mode.

**Day-1 gate test — `tests/test_a0_atomic_transaction.py`** (this is a hard ship-blocker for Phase 0b):

```python
async def test_transaction_rolls_back_on_failure(real_ledger_client):
    """Force the second statement to fail and assert the first is rolled back."""
    client = real_ledger_client
    await client.execute("DEFINE TABLE a0_canary SCHEMAFULL")
    await client.execute(
        "DEFINE FIELD name ON a0_canary TYPE string ASSERT $value != 'forbidden'"
    )
    with pytest.raises(LedgerError):
        async with client.transaction() as txn:
            txn.execute("CREATE a0_canary:1 SET name = 'allowed'")
            txn.execute("CREATE a0_canary:2 SET name = 'forbidden'")  # ASSERT fails
    rows = await client.query("SELECT * FROM a0_canary")
    assert rows == [], (
        "Embedded SurrealKV did NOT honor BEGIN/COMMIT TRANSACTION rollback. "
        "V2 cannot ship as designed; see fallback path in §6 Phase 0b."
    )
```

If this test fails (i.e. embedded SurrealKV silently ignores `BEGIN/COMMIT` and `a0_canary:1` survives the rollback), V2 cannot ship as designed against embedded mode. **Fallback path** in priority order:

1. Switch every V2 multi-step mutation to a single `LET`-chained SurrealQL statement (`LET $h = (CREATE compliance_verdict_history ...); LET $b = (UPDATE binds_to:... SET ...); UPDATE compliance_check:... SET ...`). Single-statement is implicitly atomic in SurrealKV. Acceptable correctness; constrained query shape.
2. Move the ledger from `surrealkv://` (embedded) to a network `ws://` SurrealDB process where `begin_transaction()` is supported by the Python SDK. Largest deployment delta but cleanest API.
3. V2 doesn't ship until SurrealDB embedded gains transaction support (out-of-our-hands timeline; not a real option).

The choice between fallbacks (1) vs (2) is a Jin-tier decision; do not pick without him weighing in. **Until the gate test passes (or the fallback is committed), no Phase 1+ work begins.**

Acceptance: gate test above passes against the embedded ledger configuration we ship with. Plus a forced-failure correctness test per V2 mutation (rebind, verdict-write, baseline-advance) — every multi-step mutation, when the second statement is forced to fail, leaves zero side effects.

### Phase 1 — Schema (1–2 weeks)

**C0. Migration v5→v6**

File: `ledger/schema.py`. Add a new `_migrate_v5_to_v6` function and bump `_TARGET_SCHEMA_VERSION`.

Schema additions:
- `binds_to`: `tombstoned_at`, `tombstone_reason`, `tombstone_verdict_id`, `baseline_content_hash`, `baseline_commit_hash`, `binding_version`.
- `code_region`: `symbol_name` (qualified, e.g. `module.Class.method`); rename `start_line` / `end_line` semantics to "snapshot/hint" (no schema change, just docstring).
- New table `compliance_verdict_history` (see §5.5).
- Replace `idx_cc_cache_key` with the new 7-field unique index.

Migration behavior (Codex pass-6 #3): legacy `compliance_check` rows go to history with `stale=true, stale_reason='legacy_pre_v6_no_cas_metadata'`. Drop and recreate the projection table empty.

`derive_status` (`ledger/status.py:178-205`) rewritten to read per-binding `baseline_content_hash` from `binds_to` instead of shared `code_region.content_hash`.

**C0a. Traversal filtering**

Touch every `binds_to` consumer site. `grep -rln "binds_to" handlers/ ledger/` is the worklist:

- `ledger/queries.py` — central query helpers (highest leverage; if filtered here, callers inherit it).
- `ledger/adapter.py` — direct graph walks (`get_decisions_for_file`, `get_regions_for_decision`, etc.).
- `handlers/bind.py` — idempotency check ("is this binding already present?") must consider tombstoned edges as "absent" so re-binding clears the tombstone instead of erroring on the unique index.
- `handlers/resolve_compliance.py` — verdict write path (already updated in Phase 0a).
- `handlers/history.py` — audit views: should show tombstoned edges *with* their tombstone metadata, not hide them.
- `ledger/schema.py` — schema definition only; no query changes.

Add a single helper `binds_to_active_filter()` returning the SurrealQL clause `tombstoned_at IS NONE`. Use it in every traversal site **except `history`** (which should surface tombstones with metadata).

Acceptance test: insert a tombstoned `binds_to` edge and assert:
- `detect_drift` does not surface it.
- `decision_status` projection does not count it.
- `search_decisions` graph walk does not return it.
- `bind` treating it as "absent" succeeds (clears tombstone) without violating the unique index.
- `history` *does* return it with tombstone metadata visible.

### Phase 2 — Sync barrier (1 week)

**A2a. Commit-time barrier**

File: `handlers/sync_middleware.py`. Extends V1's `repo_write_barrier`.

Two new pieces:

1. `SyncToken{head_sha, sync_at, ledger_version}` returned from a new `require_ledger_synced(ctx) -> SyncResult` (fail-closed for code-shape handlers; fail-open `ensure_ledger_synced` stays for read paths).
2. `RegionFingerprint{region_id, file_path, symbol_name, resolved_start_line, resolved_end_line, resolved_content_hash, binding_version, file_mtime, file_size}` snapshotted at sync time, re-verified at commit time.

Wire into:
- `handle_bind` (already wraps `repo_write_barrier`; add token + fingerprint check)
- `handle_resolve_compliance` (gate fail-closed; was V2 prereq for Phase 0a but full barrier lands here)
- New mutators when they're written in Phase 4

Tests cover: in-process race, out-of-process HEAD race, working-tree race (HEAD stable, file edited), file-move race (path changes without HEAD move), read-path-unaffected.

### Phase 3 — Read-path with cache (~1 week)

**C1. Cache lookup with full CAS**

File: `ledger/status.py`. Update `derive_status` (and any other compliance-cache reader) to query the projection by all 7 CAS fields, not just `(decision_id, region_id, content_hash)`.

For each binding in scope:
1. Re-resolve symbol via `resolve_symbol_lines(file_path, symbol_name)` to find current span.
2. Compute live `content_hash` over resolved bytes.
3. Read live `commit_hash` (current HEAD), `file_path`, `binding_version`, `tombstone_verdict_id`.
4. Query the projection (or history fallback per C0). Hit returns cached verdict; miss emits to `pending_compliance_checks`.

Acceptance:
- Identical state → cached verdict, no L3.
- Same content hash at different commit → cache miss → fresh L3 dispatched.
- Same content hash at different `binding_version` → cache miss (test: this binding's `advance_baseline` or rebind bumped version → next call misses; **other decisions bound to same region keep their cache hits**).
- Same content hash at different `tombstone_verdict_id` → cache miss.
- Line-shift no-op (insert blank lines above bound symbol) → cache hit, no spurious drift.

**C3. `pending_compliance_checks` from `detect_drift`**

File: `handlers/detect_drift.py`. For every region where stored `content_hash` ≠ live `content_hash`, append to `pending_compliance_checks`. The B1 AST classifier sets `cosmetic_hint: true` as metadata only — **does not** gate L3 dispatch (Codex pass-5 #3).

Per-entry payload: `{decision_id, region_id, cas_token, cosmetic_hint, diff_summary}`.

Cache short-circuit: before emitting, run the C1 cache lookup with the full CAS tuple — only emit if no cached verdict exists.

### Phase 4 — Mutating tools (2–3 weeks)

**C2. `bicameral_judge_drift` + `record_compliance_verdict`**

New MCP tool: returns `{decision_text, code_before, code_after, diff, cas_token: {expected_content_hash, expected_commit_hash, expected_file_path, expected_binding_version, expected_tombstone_verdict_id}}` for caller LLM. Mirrors the existing `bicameral_judge_gaps` pattern.

`record_compliance_verdict(decision_id, region_id, cas_token, verdict, confidence, explanation, agent_id)` handler:

1. Read current state under A2 + A2a barrier: fetch `actual_*` for all 5 CAS fields.
2. CAS check: if any `expected_*` ≠ `actual_*`, the verdict is stale.
   - Stale path: insert into `compliance_verdict_history` with `stale=true, stale_reason='<specific>_mismatch'`. Return `{stale_verdict: true, mismatched_fields: [...], current_cas_token: {...}}`. **Do not** mutate live state. **Do not** touch the projection.
3. Fresh path — **all writes happen in a single atomic SurrealQL transaction (A0), keyed on POST-MUTATION state** (pass-13 fix):
   1. **Compute the post-mutation tombstone identity first** — *before* any write — so the projection key matches live state at commit time:
      - `compliant` / `drifted` → `post_tombstone_verdict_id = ''` (any prior tombstone is cleared by this verdict).
      - `not_relevant` → `post_tombstone_verdict_id = <new_history_id>` (the row about to be inserted).
   2. Open transaction (A0). Inside the same transaction:
      a. `INSERT` into `compliance_verdict_history` (returns `<new_history_id>`).
      b. `UPDATE binds_to` to apply the post-mutation tombstone state — set `tombstoned_at`, `tombstone_reason`, `tombstone_verdict_id = post_tombstone_verdict_id` for `not_relevant`; clear those fields for `compliant`/`drifted`.
      c. `UPSERT compliance_check` projection keyed on the **post-mutation** 7-tuple `(decision_id, region_id, content_hash, commit_hash, file_path, binding_version, post_tombstone_verdict_id)`. The same `binding_version` is preserved; only `tombstone_verdict_id` changes between verdicts.
   3. Commit the transaction. If any step fails the entire write is rolled back (no orphaned history row, no half-tombstoned binding, no stale projection).

**Why post-mutation is mandatory** (pass-13 finding #2): the projection contract in §5.5 states the cache key includes `tombstone_verdict_id`. If the projection upsert uses pre-mutation values for that field and the binding tombstone state is then mutated, the projection row's key no longer matches live state — future cache lookups (which use live `tombstone_verdict_id`) miss the just-written row. The verdict effectively orphans itself in the cache. Computing the final tuple before the writes and applying everything atomically eliminates that window.

**Acceptance test for the post-mutation contract** (must ship with C2):

```
test_not_relevant_then_restore_cycle:
  1. live binding: tombstone_verdict_id=''
  2. record_compliance_verdict(verdict='not_relevant') → returns history_id_1
  3. assert binds_to.tombstone_verdict_id == history_id_1
  4. assert exactly ONE compliance_check row exists for this binding,
     keyed on tombstone_verdict_id == history_id_1 (NOT '')
  5. cache lookup with current live CAS hits that row — verdict == 'not_relevant'
  6. bicameral_restore_binding(expected_tombstone_verdict_id=history_id_1)
     → returns history_id_2 (synthetic 'restored' row)
  7. assert binds_to.tombstone_verdict_id == ''
  8. assert TWO compliance_check rows exist (history_id_1 keyed and '' keyed),
     OR projection cleanup also re-keyed the prior row — design choice,
     but the live cache lookup MUST hit the row matching live state.
  9. cache lookup with current live CAS hits the post-restore row.
```

`bicameral_restore_binding(decision_id, region_id, expected_tombstone_verdict_id)` — operator/caller tool. Includes the tombstone verdict id as a CAS token to ensure the operator is restoring the tombstone they intended.

**B3. `bicameral_advance_baseline`**

`bicameral_advance_baseline(decision_id, region_id, cas_token, verdict_id)` — verdict_id must reference a `compliance_verdict_history` row whose `verdict='compliant'`, `stale=false`, and **all five CAS components** match the call. Older verdicts (different `binding_version` or `file_path`) are **rejected** even if bytes match.

Writes new `binds_to.baseline_content_hash` and `baseline_commit_hash` for this single edge; bumps `binds_to.binding_version`. **Does not touch shared `code_region` state.** Other decisions bound to the same region are unaffected.

Inserts a `baseline_advance` audit row: `{advanced_at, decision_id, region_id, prev_baseline_hash, new_baseline_hash, prev_binding_version, new_binding_version, verdict_id, agent_id}`.

**No `ast_cosmetic` reason** — AST classification alone never advances the baseline. Only fresh L3 `compliant` verdicts can.

**D2. `bicameral_rebind` (two-phase + lease + attempt-id locking)**

The full protocol — including schema additions, lease/expiry recovery, force-supersede semantics, and the per-attempt CAS verification in phase 2 — lives in **§5.6**. This bullet is a pointer to that section, not a re-spec; read §5.6 for implementation.

**Critical edge-vs-region distinction** (pass-14 #4): `binding_version` lives on the per-`binds_to`-edge, never on `code_region`. The mutations in phase 1 are:

- **Bump `binding_version` on the OLD `binds_to` edge** — invalidates any in-flight verdicts that were authored against the old edge before the rebind started.
- **The NEW `binds_to` edge** is either freshly created (born with `binding_version=1`) or, if a tombstoned binding to the same `code_region` already exists, *reused* with its `binding_version` bumped and tombstone fields cleared.
- **`code_region` is never modified by rebind.** A move/rename creates (or reuses) a *new* `code_region` row; the old `code_region` stays immutable for audit. Per-region versioning was rejected in pass-8 specifically because shared region state corrupts cross-decision baselines (see §5.2).

If you write the implementation and find yourself reaching for `UPDATE code_region:* SET binding_version = ...`, you've reintroduced the rejected design — stop and re-read §5.2 + §5.6.

Phase 2 happens through `record_compliance_verdict` per §5.5 + §5.6: a `compliant` verdict on a `pending_verification=true` new binding atomically tombstones the old binding's `binds_to` edge and clears the lock. `drifted` / `not_relevant` verdicts leave both edges live with the lock held; caller advances via abandon, force_supersede, or lease expiry per §5.6.

### Phase 5 — Polish (2–3 days)

- **Doctor SKILL.md rendering**: update `.claude/skills/bicameral-doctor/SKILL.md` to render `pending_compliance_checks` and `pending_grounding_checks` as actionable advisories now that V2 has the safe atomic rebind. Update the verification instruction text in `handlers/link_commit.py::_build_verification_instruction` to point at `bicameral_rebind` for relocation cases (replacing the V1 "INFORMATIONAL ONLY — wait for V2" warning).
- **Codex pass-13**: re-run the adversarial review on the final V2 implementation. Target: clean ship with no remaining critical findings.
- **Convert scenario 8** in `tests/test_desync_scenarios.py` from `@pytest.mark.xfail(strict=True)` to a normal expected-pass test that exercises the two-phase rebind end-to-end.

### Effort estimate by phase

| Phase | Estimated effort (single owner, sequential) |
|---|---|
| 0 (prereq) | 1–2 weeks |
| 1 (schema) | 1–2 weeks |
| 2 (barrier) | 1 week |
| 3 (reads) | ~1 week |
| 4 (writes) | 2–3 weeks |
| 5 (polish) | 2–3 days |
| **Total** | **7–10 weeks** |

Phases don't parallelize cleanly because each depends on prior invariants. If multiple engineers, they can work on different deliverables *within* a phase (e.g. C0 vs C0a in Phase 1) but should not skip ahead.

---

## 7. Constraints catalog

This is the synthesized "what NOT to ship" guide. Each entry came from a Codex review pass that found a real bug in an earlier V2 design draft. Twelve passes total. Following these constraints is the difference between V2 shipping safely and V2 introducing data-corruption regressions.

### 7.1 The recurring root cause

Every Codex pass found a place where authoritative state was being mutated (or cached) without authoritative proof of the state being mutated. **V2 must commit to a uniform contract: no live mutation without a fresh, full-CAS verdict from the same call, applied via a single atomic SurrealQL statement.**

If you're tempted to add a new mutating tool that doesn't follow this pattern, you've found the next regression.

### 7.2 No mutation without authoritative proof

(Aggregated from passes 1, 2, 3, 4, 5, 7, 8.)

- Every mutating tool must take a CAS token. The token has 5 components: `expected_content_hash`, `expected_commit_hash`, `expected_file_path`, `expected_binding_version`, `expected_tombstone_verdict_id`.
- Mismatch on **any** component → record verdict in history with `stale=true, stale_reason='<specific>_mismatch'` and **do not** mutate live state.
- `expected_commit_hash` is **mandatory**, not optional. Same content hash can legitimately appear at a different HEAD (revert), at a different file path (move), at a different `binding_version` (rebaseline / rebind), or with a different `tombstone_verdict_id` (operator action).
- `expected_binding_version` is per-`binds_to`-edge, not per-region. Per-region versioning was rejected because it lets one decision's actions invalidate another decision's cache (cross-decision corruption).
- Tombstone state is part of identity. Operator restoration / re-tombstoning produces a different cache row, so verdicts authored against an old tombstone state never replay against a current restored state.

### 7.3 No backdoor paths

(Aggregated from passes 2, 6, 10.)

- New safety contracts must be applied to **every** caller path day one. No "small contract change for existing flow" deferred to later — Codex's pattern was that every "follow-up" became the next exploit.
- **`handlers/resolve_compliance.py:122`** still hard-deletes `binds_to` on `not_relevant`. **`handlers/ingest.py:313-331`** auto-chains into it via `handle_judge_gaps`. Both must move to tombstone + full CAS **before** any new mutating tool ships. This is the single highest-leverage move and it's the **hard prerequisite** for the rest of V2 (Phase 0a).
- Migration of legacy `compliance_check` rows: do **not** backfill from current state. Insert into `compliance_verdict_history` with `stale=true, stale_reason='legacy_pre_v6_no_cas_metadata'`, drop and recreate the projection empty. Backfilling fabricates CAS metadata that was never recorded historically.

### 7.4 Identity & CAS dimensions

(Aggregated from passes 3, 5, 7, 8, 9.)

The CAS tuple converged after 9 passes to the following **single source of truth** — V2 must use this verbatim across schema, lookup, write upsert, and acceptance tests:

```
(decision_id, region_id, content_hash, commit_hash, file_path,
 binding_version, tombstone_verdict_id)
```

Where:
- `content_hash` = hash over **resolved-symbol bytes**, not bytes at frozen line range. Region identity is `(file_path, symbol_name)`; line numbers are advisory snapshots, re-resolved on every read via `resolve_symbol_lines()` (`ledger/status.py:21-89`).
- `binding_version` lives on the `binds_to` edge, not on `code_region`. **Per-binding ownership is mandatory** — shared region state corrupts cross-decision baselines.
- `tombstone_verdict_id` is part of the cache key so operator restoration / re-tombstone produces a different cache row, never a hit.
- Same content hash at different commit / path / binding_version / tombstone_verdict_id is a **different** projection row, not an overwrite.

### 7.5 Atomicity & race windows

(Aggregated from passes 4, 6.)

- Embedded SurrealKV does **not** support client-side `begin_transaction()`. V2 uses inline SurrealQL `BEGIN TRANSACTION; ...; COMMIT TRANSACTION;` blocks submitted as a single `query()` call via the `LedgerClient.transaction()` context manager (A0 in §6 Phase 0b — committed choice, not optional). Day-1 gate test in §6 Phase 0b proves rollback works in our deployment mode; explicit fallback path (single `LET`-chained statements, then network SurrealDB) is documented if the gate fails.
- **Verify the embedded SurrealKV mode honors BEGIN/COMMIT semantics** — some SurrealDB modes silently ignore them. Day-1 spike.
- Sync barrier must extend from handler entry to commit time. HEAD-only CAS is insufficient — working-tree edits race writes without changing HEAD. Per-region fingerprint CAS at commit time required for `bicameral_rebind`, `bicameral_advance_baseline`, and `record_compliance_verdict`.
- Forced failure on the second statement of a multi-step mutation must leave zero side effects (no orphaned new edge, no half-tombstoned old edge, no history-without-projection).

### 7.6 Verdict semantics

(Aggregated from passes 1, 2, 3, 11.)

- Verdicts must be **reversible**. Storage requires append-only `compliance_verdict_history` + projection — the legacy `compliance_check` UNIQUE index makes reversal physically impossible.
- `not_relevant` verdicts must **tombstone, not hard-delete**. Restoration must be auditable via a `bicameral_restore_binding` tool with its own CAS token.
- **D2 (`bicameral_rebind`) is two-phase**. The single-transaction "create new + tombstone old" version retires the authoritative binding before the new target is semantically proven — a wrong candidate selection silently reattaches the decision to unrelated code. Phase 1: create new as pending, return CAS token. Phase 2: caller's L3 verdict on new target gates atomic tombstoning of old.

### 7.7 AST classifier discipline

(From pass 7.)

- The B1 whitelist must be narrow: intra-line whitespace, trailing whitespace, blank lines between statements. **That's it.**
- Trailing commas, **all** comment edits, docstring edits are **not** cosmetic. Trailing commas are behavioral in Python (`(x,)` vs `(x)`); comments carry tool directives (`# type: ignore`, `// @ts-ignore`, build tags); docstrings are observable via `__doc__`.
- The classifier **never gates L3 dispatch**. All hash-divergent regions reach L3 with the hint as advisory metadata only.

### 7.8 Spec writes vs code-shape writes

(From pass 6 #1.)

- **Spec writes** (`ingest`, `ratify`) must remain append-only with best-effort post-write sync. Fail-closing them on git/repo outage drops the user's decision — strictly worse than today's desync.
- **Code-shape writes** (`bind`, `resolve_compliance`, all V2 destructive tools) must be fail-closed on sync failure.

### 7.9 Pass-12 specific findings (V1 pre-ship review)

(From pass 12, addressed in V1 commit `a04e54b`.)

- The v0.6.4 monolithic `_VERIFICATION_INSTRUCTION` indiscriminately routed both ungrounded and `symbol_disappeared` cases to a `bicameral.bind` CTA. For relocation cases, that creates duplicate-binding state.
- V1 split the instruction into per-`reason` parts. **V2 retains this split** even after atomic rebind ships; the relocation branch is updated to point at `bicameral_rebind` instead of warning callers off.
- V1's claim that the doctor SKILL.md "is already advisory" was empirically false — the file at `.claude/skills/bicameral-doctor/SKILL.md` (note path; not `skills/bicameral-doctor/`) contains zero references to `pending_grounding_checks`, `relocation`, `symbol_disappeared`, or `bicameral.bind`. V2 Phase 5 polish updates the skill to render these.

### 7.10 Pass-13 specific findings (V2 design review)

Two high-severity findings on the V2 design itself, addressed in §5.5 and §5.6.

**Rebind phase 2 must verify the specific pending attempt, not just "the old binding."**

A naive two-phase rebind whose phase 2 only carries the *new* binding's CAS token can tombstone the wrong old binding when a caller has done multiple phase-1 attempts. A stale phase-2 verdict for an abandoned candidate would still trigger old-edge tombstoning even if the caller intended to use a different candidate. The fix has three parts:

1. **Single pending rebind per old binding**: phase 1 sets `binds_to.pending_rebind_attempt_id = <attempt_id>`. Concurrent phase-1 attempts on the same `old_region_id` see the lock and abort with `rebind_already_pending`.
2. **Immutable attempt id**: `rebind_audit.attempt_id` is a UUID, generated in phase 1, stored on both the new binding (`binds_to.rebind_attempt_id`) and the audit row. Phase 2 carries no extra arg — the new binding's `rebind_attempt_id` field is the link.
3. **Phase 2 cross-CAS**: when handling a `compliant` verdict on a `pending_verification` new binding, the verdict handler re-reads the OLD binding and verifies `old_binding.pending_rebind_attempt_id == new_binding.rebind_attempt_id` AND that the snapshotted `old_binding_version_at_attempt` / `old_tombstone_verdict_id_at_attempt` from the audit row still match. Mismatch → record stale-history-only with `stale_reason='rebind_attempt_superseded'`. The old binding is never tombstoned by a stale verdict.

Explicit abandon path (`bicameral_abandon_rebind`) lets a caller supersede a prior attempt cleanly, so the protocol doesn't hang on an indecisive caller. See §5.6 for the full schema and protocol.

**`record_compliance_verdict` must derive projection keys from POST-mutation state, not pre-mutation inputs.**

The cache contract (§5.5) says `compliance_check` is keyed on the full 7-tuple including `tombstone_verdict_id`. If the verdict-write algorithm upserts the projection BEFORE mutating `binds_to.tombstone_verdict_id`, the cached row is keyed on the old tombstone identity while the live binding immediately changes to a different tuple. Future cache lookups use the live `tombstone_verdict_id` and miss — verdict orphans itself in the cache; the user sees a stale "no cached verdict" state and the L3 round-trip is repeated unnecessarily.

The fix: compute `post_tombstone_verdict_id` (`''` for compliant/drifted, `<new_history_id>` for not_relevant) BEFORE any write, then in a single atomic transaction insert the history row → update binds_to → upsert the projection keyed on the post-mutation tuple. The acceptance test in §6 Phase 4 (C2) — `test_not_relevant_then_restore_cycle` — must ship alongside the `record_compliance_verdict` implementation to lock the contract. See §5.5 / §6 Phase 4 for the full algorithm.

The general rule extracted from this finding: **whenever a write mutates a field that is part of any cache or index key, the cache/index write must be derived from the post-mutation value of that field, not the pre-mutation input.** This applies to verdict-writes (tombstone_verdict_id), baseline-advance (binding_version on the relevant `binds_to` edge), and rebind (binding_version on both the old and new `binds_to` edges). All three must compute final state first, then atomically commit the cluster of changes.

### 7.11 Pass-14 specific findings (V2 guide review)

Four findings on the consolidated V2 guide itself, addressed in §6 Phase 0b, §5.6, §8, and §6 Phase 4 D2.

**Atomicity is a committed choice, not an open option** (pass-14 #1).

The previous draft listed Opt 1 (`LedgerClient.transaction()` wrapping `BEGIN/COMMIT TRANSACTION`) and Opt 2 (single `LET`-chained statement) as alternatives and deferred picking. That defers the prerequisite of every destructive path in V2. The guide now commits to Opt 1, ships a day-1 forced-failure gate test (`test_transaction_rolls_back_on_failure` in §6 Phase 0b) that proves embedded SurrealKV honors `BEGIN/COMMIT/CANCEL TRANSACTION` rollback semantics, and documents an explicit fallback path (Opt 2 first, then network SurrealDB second) if the gate fails. **No Phase 1+ work begins until the gate test passes against the embedded ledger configuration we ship with.**

**Pending rebinds must have a server-enforced lease** (pass-14 #2).

The previous draft only documented a caller-driven `bicameral_abandon_rebind` path. A crashed or distracted caller could wedge an `old_region_id` indefinitely (every subsequent `bicameral_rebind` returns `rebind_already_pending`). The fix adds:

- `rebind_audit.expires_at` field, populated from `BICAMERAL_REBIND_LEASE_SECONDS` (default 24h).
- An on-demand expiry sweep at the start of every `bicameral_rebind` phase 1 — atomically abandons any expired pending attempt before issuing a new one (`outcome='abandoned_by_expiry'`).
- A `force_supersede=true` flag on `bicameral_rebind` for explicit caller-driven supersession.
- Phase 2 lease check in `record_compliance_verdict`: if the audit row's outcome is no longer `pending` or `expires_at < now()`, the verdict is recorded with `stale=true, stale_reason='rebind_attempt_expired' / '_superseded' / '_abandoned'` and the old binding is never tombstoned.

The combination guarantees forward progress: no client crash can wedge a binding for longer than the lease TTL, even without operator intervention.

**`judge_gaps` parity is resolved, not deferred** (pass-14 #3).

The previous draft listed "judge_gaps parity" as an open question while §7.3 simultaneously claimed all backdoor paths must be closed before new tools ship. That's a contradiction. Resolved in §8 question 5: `bicameral_judge_gaps` is read-only (returns a context pack to the caller LLM, never writes). The destructive write happens in `handlers/resolve_compliance.py`, which Phase 0a migrates from hard-delete to tombstone+CAS. Phase 0a covers the entire judge_gaps→resolve_compliance pipeline; no separate change to `judge_gaps` itself is required. §7.3's "all backdoors closed before new tools ship" claim stands.

**`binding_version` lives on edges, never on regions** (pass-14 #4).

The previous draft's §6 Phase 4 D2 summary said to "bump `binding_version` on both old and new regions" — region terminology, contradicting the §5.2 design where versioning is per-binding (per-edge) specifically to avoid cross-decision corruption. Rewritten: every mention of `binding_version` mutation is now in edge terminology (`binds_to`), `code_region` is explicitly called out as immutable under rebind, and the bullet warns against the exact misimplementation the pass-14 reviewer flagged. If an implementer reaches for `UPDATE code_region:* SET binding_version`, they've reintroduced the rejected design.

---

## 8. Open questions

These need human judgment before V2 implementation starts. Codex's adversarial review can't answer them.

1. **Phase 0a vs 0b ordering.** Should `resolve_compliance` migrate to tombstone (Phase 0a) before A0 lands, or after? Doing it first closes the destructive backdoor sooner but uses a single-statement `UPDATE` (no transaction needed). Doing it after A0 means we can do the migration as a transaction-wrapped multi-step write. Recommend Phase 0a first; document the sequencing decision in the V2 kickoff.

2. ~~**Transaction primitive: opt 1 vs opt 2.**~~ **Resolved (pass-14 #1)**: V2 commits to `LedgerClient.transaction()` wrapping inline `BEGIN/COMMIT TRANSACTION` blocks. The day-1 gate test in §6 Phase 0b verifies embedded SurrealKV honors rollback; if that test fails, the explicit fallback path (single `LET`-chained statements, then network SurrealDB) ships instead. No deferred decision — see §6 Phase 0b for the committed mechanism and §7.11 for the rationale.

3. **Cache projection vs history-only.** Keep `compliance_check` projection table for perf, or serve cache reads directly from `compliance_verdict_history` via `WHERE all_seven_CAS_components_match AND stale=false ORDER BY recorded_at DESC LIMIT 1`? Both are semantically equivalent given the migration empties the projection. Decision deferred to A1 benchmark numbers run against the history-only path.

4. **Tombstone GC policy.** How long do tombstoned `binds_to` rows live before hard-delete? Candidate: 30 days with no contradicting verdict, plus operator-callable purge. Aligns with retention policy. Could also defer entirely to V3.

5. ~~**`judge_gaps` parity.**~~ **Resolved (pass-14 #3)**: `bicameral_judge_gaps` itself is read-only — it returns a context pack to the caller LLM and never writes. The only write that happens in the gap-judgment flow is when the caller LLM calls `bicameral.resolve_compliance` to record the verdict. Phase 0a's migration of `handlers/resolve_compliance.py` from hard-delete to tombstone+CAS therefore covers the entire pipeline; no separate contract change to `judge_gaps` is needed. **The §7.3 statement that "all backdoor paths are closed before new tools ship" stands and is not in conflict with this entry.** Removing this question from the open list.

6. **Catch-up latency budget for write path.** A1+A3 in V1 measured the read-path baseline. V2 inherits with stricter SLOs. If barrier-held p95 > 1s under realistic load, may need finer-grained locking (per-decision, per-region) — tracked but not in V2 scope unless measurements force it.

7. **Should V2 ship as one PR or several?** 7-10 weeks of work + 6 phases is a lot for a single PR. Recommend phase-aligned PRs: Phase 0, Phase 1, Phase 2, Phase 3, Phase 4 split per tool (C2 / B3 / D2 each as its own PR), Phase 5 polish. Each PR re-runs Codex.

8. **Who owns V2?** CODEOWNERS requires Jin approval. Recommend involving him at design time, not just at PR-review time. He should weigh in on at least the open questions above.

---

## 9. Acceptance criteria for V2

V2 is shippable when **all** of the following hold:

### Quantitative thresholds

- [ ] Scenario 8 in `tests/test_desync_scenarios.py` flips from `xfail(strict=True)` to expected pass — atomic rebind end-to-end test.
- [ ] Full desync scenario suite: **13 / 13 pass, 0 xfail**.
- [ ] Catch-up latency p95 < 1000 ms on the V1 benchmark fixture (A1).
- [ ] No regression on V1 perf baseline: search_decisions p95 ≤ 11 ms, detect_drift p95 ≤ 17 ms (allows 1.5× headroom over V1's 10.4ms / 15.5ms).
- [ ] Forced-failure correctness tests for atomicity (Phase 0b): every multi-step mutation, when the second statement is forced to fail, leaves zero side effects.
- [ ] Codex review pass-13 produces zero remaining critical (high-severity) findings.

### Qualitative / behavioral

- [ ] `handlers/resolve_compliance.py` no longer calls `delete_binds_to_edge` — replaced by tombstone path with full CAS.
- [ ] All `binds_to` traversal sites filter via `binds_to_active_filter()` (audited via grep).
- [ ] `derive_status` reads per-binding `baseline_content_hash` from `binds_to`, never shared `code_region.content_hash`.
- [ ] Every mutating handler takes a 5-field CAS token; mismatch produces a stale-history row and zero live mutation.
- [ ] `bicameral_rebind` is two-phase **with attempt-id locking** (pass-13 #1): phase 1 sets `binds_to.pending_rebind_attempt_id`; concurrent phase-1 attempts on the same `old_region_id` get `rebind_already_pending`; phase 2's verdict handler verifies `old_binding.pending_rebind_attempt_id == new_binding.rebind_attempt_id` AND the audit row's snapshotted old-binding state still matches before any tombstoning. Stale phase-2 verdicts on superseded attempts produce `stale_reason='rebind_attempt_superseded'` history rows and **never** tombstone old. `bicameral_abandon_rebind` exists for explicit caller-driven supersession.
- [ ] `record_compliance_verdict` derives the projection key from **post-mutation state** (pass-13 #2): `post_tombstone_verdict_id` computed before any write; history insert + binds_to update + projection upsert all in one atomic A0 transaction; the projection row's CAS tuple matches live `binds_to` state at commit time. Acceptance test `test_not_relevant_then_restore_cycle` must pass — proves cache lookups with current live CAS hit the row matching live state across a full not_relevant → restore cycle.
- [ ] **A0 gate test passes** (pass-14 #1): `tests/test_a0_atomic_transaction.py::test_transaction_rolls_back_on_failure` succeeds against the embedded ledger configuration we ship with — proves `BEGIN/COMMIT TRANSACTION` rollback semantics actually work. If gate fails, the documented fallback path is followed and the alternative mechanism passes its own forced-failure correctness tests.
- [ ] **Rebind has lease-driven recovery** (pass-14 #2): `rebind_audit.expires_at` populated on phase 1; on-demand expiry sweep at the start of every `bicameral_rebind` phase 1 atomically abandons stale leases (`outcome='abandoned_by_expiry'`); phase 2 lease check rejects verdicts on expired/superseded/abandoned attempts as stale-history-only; `force_supersede=true` on `bicameral_rebind` provides explicit caller-driven supersede. Acceptance test simulates a crashed caller (insert stale `rebind_audit` with `recorded_at - 25h`) and proves the next `bicameral_rebind` succeeds with the prior attempt marked `abandoned_by_expiry`.
- [ ] **Edge-vs-region terminology audit** (pass-14 #4): grep proves no V2 implementation code mutates `binding_version` on `code_region`. Every `binding_version` write targets a `binds_to` edge.
- [ ] **`judge_gaps` migration is implicit, not separate** (pass-14 #3): Phase 0a's `resolve_compliance` migration covers the entire `judge_gaps → resolve_compliance` pipeline. No separate code change to `handlers/gap_judge.py` (read-only) is required or made.
- [ ] `.claude/skills/bicameral-doctor/SKILL.md` renders `pending_compliance_checks` and `pending_grounding_checks` with the (now-safe) bind / rebind flows.
- [ ] CHANGELOG entry summarizes V2 deliverables; the V1 "Unreleased" entry can roll up into a V2 release version (or both can ship as a single release, depending on team preference).

### Documentation

- [ ] `TODO.md` and `PLAN.md` ticked for every V2 phase deliverable per the project's auto-tick mandate.
- [ ] This guide (`docs/v2-desync-optimization-guide.md`) updated with V2 shipped status; or replaced with a V3 doc if the cycle continues.

---

## 10. References

### Local files V2 will touch

- `ledger/schema.py` — schema migration v5→v6
- `ledger/client.py` — A0 transaction primitive
- `ledger/status.py` — `derive_status` per-binding rewrite
- `ledger/adapter.py` — `ingest_commit` updates, traversal filtering
- `ledger/queries.py` — SurrealQL helpers, `binds_to_active_filter`
- `handlers/sync_middleware.py` — A2a barrier
- `handlers/bind.py` — A2a wiring, post-tombstone idempotency
- `handlers/resolve_compliance.py` — Phase 0a hard-delete → tombstone
- `handlers/ingest.py` — no V2 contract change required (it auto-chains into `handle_judge_gaps`, which is read-only; the destructive write happens later in `resolve_compliance`, which Phase 0a migrates). Listed here for awareness, not for editing.
- `handlers/detect_drift.py` — C3 cache-aware emission
- `handlers/link_commit.py` — verification_instruction text update for V2 rebind path
- `contracts.py` — new contracts for verdict / advance_baseline / rebind responses, CAS token types
- `server.py` — register new MCP tools
- `tests/test_desync_scenarios.py` — convert scenario 8 from xfail to pass
- `tests/test_resolve_compliance.py` — assert tombstone, not deletion
- New tests: `tests/test_record_compliance_verdict.py`, `tests/test_advance_baseline.py`, `tests/test_rebind.py`, `tests/test_a2a_barrier.py`, `tests/test_v6_migration.py`
- `.claude/skills/bicameral-doctor/SKILL.md` — Phase 5 rendering update

### V1 commits on this branch

```
8e226c5 docs: tick V1 desync optimization across CHANGELOG / TODO / PLAN
a04e54b fix(link_commit): split verification_instruction so relocation cases don't get bind CTA
89f8076 feat: desync optimization V1 F1 — canonical 13-scenario regression matrix
54081e6 feat: desync optimization V1 D1 — original_lines on symbol-disappeared payload
401babc feat: desync optimization V1 Phase B — read-path cosmetic-change advisory
3b4d0bb feat: desync optimization V1 Phase A — measurement + light sync hardening
```

### Notion references

- [The Auto-Grounding Problem: Keeping Decisions Linked to Code](https://www.notion.so/3332a51619c4813caccec86c36d9bf98) — 13 desync scenarios, Compliance Reframe (L1/L2/L3 model)
- [The Branch Problem: Git Branches in the Decision Ledger](https://www.notion.so/3302a51619c48146b48dc675914beb6f) — content-hash primitive rationale
- [CI Workflow Fixes — MCP Regression Pipeline (Apr 8)](https://www.notion.so/33c2a51619c48134ba8dc8bfaeb880dd) — PR #84 scorecard shift from 77% → 92%; the "tests must use real handler layer" lesson

### External technical references

- [SurrealDB Python SDK — Connecting](https://surrealdb.com/docs/sdk/python/concepts/connecting-to-surrealdb) — embedded mode does NOT support `begin_transaction()`. V2 must use inline SurrealQL.
- [SurrealQL Transactions](https://surrealdb.com/docs/surrealql/transactions) — `BEGIN/COMMIT/CANCEL TRANSACTION` semantics. **Verify embedded SurrealKV honors these.**
- [SurrealQL COMMIT statement](https://surrealdb.com/docs/surrealql/statements/commit)
- [SurrealKV README](https://github.com/surrealdb/surrealkv) — "embedded ACID-compliant key-value storage engine"
- [py-tree-sitter](https://github.com/tree-sitter/py-tree-sitter) — used by V1 B1 (`ledger/ast_diff.py`); V2 may extend with semantic-equivalence checks.
- [Tree-sitter whitespace handling — issue #497](https://github.com/tree-sitter/tree-sitter/issues/497) — confirms tree-sitter does not represent inter-token whitespace as nodes.
- [Diffsitter HN discussion](https://news.ycombinator.com/item?id=27875333) — prior art for AST-based semantic diff.

### Codex review history

The V2 design doc went through 9 review rounds plus 3 review rounds on the V1 plan (12 total) before V1 shipped. Each round found a bug in the prior draft. The synthesis of those findings is §7. If you want the chronological record (useful for understanding *why* a constraint exists), `git log --all --diff-filter=D --name-only -- docs/desync-optimization.md` after the old docs are deleted will show the file's last state.

The pattern across all 12 passes was: "V2 keeps adding safety contracts to new code paths but leaves backdoors in old paths." Phase 0 (resolve_compliance migration) addresses the most prominent instance. **If you find yourself adding a new mutating tool that doesn't migrate every existing path that touches the same data, you're recreating the pattern.**

---

## Final note for the engineer / agent picking this up

V2 is a high-risk, high-reward change. The risk surfaced naturally over 12 review passes and is now well-characterized in §7. The reward — actual semantic drift detection, safe rename recovery, reversible verdicts — is what bicameral was originally pitched to do.

Take your time on Phase 0. It's the foundation everything else stands on. The single best signal that V2 is going well is: every PR through Phase 4 lands with the V1 desync scenario suite still at 12/13 PASS + 1 XFAIL until D2 ships, at which point scenario 8 flips and the suite is 13/13. If at any point a different scenario starts failing or xfailing, you've regressed something — stop and root-cause before continuing.

Involve Jin. He's CODEOWNERS, knows the project trajectory, and the open questions in §8 are exactly the kind of thing he should weigh in on. Don't make him a PR-review-time discovery.

Good luck.
