# Bicameral MCP — Hackathon Task Tracking

_Replica of [Notion Task Tracking](https://www.notion.so/3232a51619c480e680eada5629cf77b4), expanded with engineering detail._

---

## Shared / Research

- [x] Create implementation plan for Agent B (code locator)
- [x] Research tools/tech stack
- [x] Revise/update implementation plan
- [x] Stage 0: Build out a skeleton (code-locator)
- [x] Research CodexGraph
- [x] Code-Locator E2E
- [x] Performance Eval (Code-Locator Agent B)
- [x] Compare tech spec against SurrealDB, CocoIndex
- [ ] Explore full auto AI coding workflow (OpenHands)
- [ ] CocoIndex: usage and how it improves baseline perf
- [ ] SurrealDB: usage and how it replaces SQLite
- [ ] Research/Plan for dynamic programming language support

---

## Jin

- [x] End-to-end product demo (Per + Pear VC + Bucky)
- [x] Set up Google devbox for Paperclip demo
- [ ] Founder video
- [ ] GTM automation (Lovis sync)
- [ ] BD automation (Ian sync)

---

## MCP Sprint

### API / Architecture

- [ ] Commit to API between modules — handoff + search_graph contracts
  - [ ] Hypothesis around agent harness — how to model "decisions" (what info lets agent infer status)
  - [x] Use case defined
  - [ ] The "decision relevance" problem (noise vs signal — hard design problem)
    - [ ] How to handle obsolete decisions

### Code Locator + CocoIndex module

- [ ] Stable `AgentResult` API from code-locator (Silong)
- [ ] CocoIndex v1 integration with SQLite (Silong)
- [x] Wire real code locator into MCP adapter (Jin — Phase 1)
- [x] Remove nested LLM from MCP server by exposing deterministic retrieval tools (Jin — Phase 1.5)

### Decision Ledger + SurrealDB

- [x] SurrealDB schema defined and initialized (Jin)
- [x] Payload ingestion: `CodeLocatorPayload` → graph (Jin)
- [x] Ledger search (SurrealDB FTS on decision descriptions) (Jin)
- [x] Symbol-decision lookup (reverse traversal) (Jin)
- [x] Wire real ledger into MCP adapter (Jin — Phase 2)

---

## Engineering Progress

_Tracks actual implementation status in `pilot/mcp/`. Updated by Claude as work completes._

### Phase 0: MCP + Mocks — DONE

#### Scaffold

- [x] `contracts.py` — MCP response types + shared sub-types
- [x] `server.py` — MCP stdio entrypoint, 9 tools registered
- [x] `requirements.txt`

#### Handlers

- [x] `handlers/decision_status.py`
- [x] `handlers/search_decisions.py`
- [x] `handlers/detect_drift.py`
- [x] `handlers/link_commit.py`
- [x] `handlers/ingest.py`

#### Adapters

- [x] `adapters/ledger.py` — real SurrealDB adapter
- [x] `adapters/code_locator.py` — real code locator adapter

### Phase 1: Real Code Locator — DONE

- [x] `adapters/code_locator.py` — `RealCodeLocatorAdapter` with lazy init
- [x] `handlers/search_decisions.py` — pure ledger read (no query-time locate)
- [x] `handlers/detect_drift.py` — real `extract_symbols()` for symbol enumeration
- [x] `mocks/code_locator.py` deleted

### Phase 1.5: MCP-native retrieval surface — DONE

- [x] Expose `validate_symbols` as an MCP tool
- [x] Expose `get_neighbors` as an MCP tool
- [x] Expose `extract_symbols` as an MCP tool
- [x] Retire the nested litellm loop — removed entirely
- [x] v0.6.4: retired `search_code` — caller LLM owns code retrieval via Grep/Read

### Phase 2: Real Decision Ledger — DONE

- [x] `adapters/ledger.py` — `SurrealDBLedgerAdapter`
- [x] All 5 handlers query real graph
- [x] `mocks/decision_ledger.py` deleted

### Phase 3: Integration — IN PROGRESS

- [x] Zero active mocks
- [x] Full E2E verified
- [x] GitHub Actions CI (replaces pre-push hook)
- [x] Performance benchmarks (V1 A1 — `tests/bench_drift.py`; baseline
      55–185× under V2 targets — see `docs/desync-optimization-v1-plan.md` §A1)
- [ ] LLM drift judge (V2 — see `docs/desync-optimization.md` §8 C2)

### Desync Optimization V1 — DONE (read-path advisory + measurement)

Plan: `docs/desync-optimization-v1-plan.md`. V2 design target:
`docs/desync-optimization.md`. V1 introduces zero new mutating paths.

- [x] **A1** — drift benchmark harness (`tests/bench_drift.py`)
- [x] **A2-light** — per-repo `asyncio.Lock` for `handle_bind`
      (`handlers/sync_middleware.repo_write_barrier`)
- [x] **A3** — sync-metrics instrumentation (`SyncMetrics` contract +
      handler-side timing on search / preflight / history / bind)
- [x] **B1** — strict-whitelist tree-sitter cosmetic-change classifier
      (`ledger/ast_diff.is_cosmetic_change`)
- [x] **B2** — `DriftEntry.cosmetic_hint` advisory metadata
      (`handlers/detect_drift._enrich_with_cosmetic_hints`)
- [x] **D1** — `original_lines` enrichment on `symbol_disappeared`
      grounding checks
- [x] **F1** — canonical 13-scenario regression matrix
      (`tests/test_desync_scenarios.py`); scorecard 12 pass + 1 V2 xfail
- [x] **pass-12 follow-up** — `_build_verification_instruction` split
      so `symbol_disappeared` cases get an explicit "do NOT call
      bicameral.bind" warning instead of the v0.6.4 monolithic CTA
      (Codex pass-10 #2 + pass-12 #2)
- [x] **incidental fix** — `ledger/adapter.py:475` was emitting empty
      `decision_id` on ungrounded grounding checks; surfaced by F1

### V2 — Deferred (destructive-path overhaul)

Tracked in full in `docs/desync-optimization.md` (nine rounds of Codex
review) and summarized in `docs/desync-optimization-v1-plan.md` §4–§5.
Hard prerequisite before V2 destructive work ships: migrate
`handlers/resolve_compliance.py` hard-delete and the
`handlers/ingest.py` auto-chained `handle_judge_gaps` to tombstone +
full-CAS semantics.

- [ ] A0 — atomic SurrealQL block primitive
- [ ] A2a — full sync barrier (token CAS + region fingerprint at commit)
- [ ] C0 / C0a / C1 — schema v5→v6 migration + traversal filtering +
      full-CAS cache key
- [ ] C2 — `bicameral_judge_drift` + `record_compliance_verdict` with
      five-field CAS (incl. binding-state)
- [ ] C3 — cache-aware `pending_compliance_checks` from `detect_drift`
- [ ] B3 — `bicameral_advance_baseline` (only after L3 `compliant`
      verdict)
- [ ] D2 — `bicameral_rebind` with old-binding CAS; closes scenario 8

### Hierarchical Decisions + Ingest Filter Redesign (v0.9.3)

From eng review 2026-04-26. Four independent workstreams — A+B+C launch in parallel, D waits on schema.

**Lane A — Skill + Evals (no server changes)** ✅
- [x] Add L1/L2/L3 tiebreaker rule to `skills/bicameral-ingest/SKILL.md`
- [x] Add eval fixtures: `tests/fixtures/ingest_level_classification/` — 7 JSON fixtures (01–07), each with `{source, expected_level, expected_route, rationale}`
- [x] Create canonical `skills/bicameral-resolve-collision/SKILL.md`: trigger now caller-LLM post `bicameral.history` — not server keyword search. Tool contract unchanged.
- [ ] Delete stale `.claude/skills/bicameral-*/SKILL.md` duplicates that have canonical counterparts. Requires deciding whether Claude Code reads from `pilot/mcp/skills/` directly or still needs `.claude/skills/` symlinks.

**Lane B — Schema v9 migration** ✅
- [x] Add `decision_level: option<string>` (`L1|L2|L3`) field to `decision` table in `ledger/schema.py`
- [x] Add `parent_decision_id: option<string>` field to `decision` table
- [x] Write `_migrate_v8_to_v9` in `ledger/schema.py` (additive — no data loss); SCHEMA_VERSION bumped to 9
- [x] Update `contracts.py`: add `decision_level` and `parent_decision_id` to `BriefDecision`; `ledger/queries.py` and `ledger/adapter.py` pass through from mapping

**Lane C — Server: remove BM25 supersession** ✅
- [x] Remove `_find_overlap_candidates` from `handlers/ingest.py`
- [x] Remove `collision_pending` signoff state logic from `handlers/ingest.py` (`collision_pending` left as orphaned valid status in schema ASSERT for rollback safety)
- [x] Remove `supersession_candidates` from `IngestResponse` and `SupersessionCandidate` from `contracts.py`
- [x] Delete `tests/test_supersession.py` (tested removed function)
- [x] Update `skills/bicameral-ingest/SKILL.md` Step 2.5: replace server-BM25 supersession flow with post-ingest `bicameral.history` call + caller-LLM classification. Cross-level matches auto-mechanical. Same-level conflicts → call `bicameral_resolve_collision`.

**Lane D — L1 identity exemption — CodeGenome-aligned** ✅
- [x] L1 decisions filtered from `pending_grounding_decisions` in `handlers/ingest.py`
- [x] L1 decisions filtered from `pending_grounding_checks` ungrounded sweep in `ledger/adapter.py`
- [x] L1 decisions skipped in `symbol_disappeared` grounding check in `ledger/adapter.py`
- [x] `decision_level` added to both `<-binds_to<-decision.{...}` traversal selects and `get_all_decisions` in `ledger/queries.py`
- [x] `_resolve_subjects_eligible` stub added to `handlers/detect_drift.py` (CodeGenome Phase 1 replaces body)
- [x] CodeGenome hook comment added in `handlers/link_commit.py` at bind path

**Lane E — CodeGenome hooks** ✅
- [x] `_resolve_subjects_eligible(decision) -> bool` in `handlers/detect_drift.py`: L2=True, L1/L3=False, None=True (backward compat)
- [x] `# CodeGenome hook: L2-only enrichment` comment in `handlers/link_commit.py`
- [x] `parent_decision_id` surfaced in ungrounded list from `ledger/adapter.py`; `decision_level` and `parent_decision_id` on `BriefDecision` in `contracts.py`

---

## Mock Registry

All mocks deleted. V1 introduces no new mocks (read-path advisory
only). See git history for the original Phase 1 / Phase 2 mock
replacements (`RealCodeLocatorAdapter`, `SurrealDBLedgerAdapter`).
