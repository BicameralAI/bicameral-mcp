# Bicameral MCP — Phased Implementation Plan

**Goal**: A working MCP server with 9 tools (5 ledger + 4 code locator), backed by real implementations.

**CI**: GitHub Actions runs Phase 1–3 regression tests on PRs to `main`. All phases use real adapters with `SURREAL_URL=memory://`.

---

## Phase 0: Complete MCP with Mocks — DONE

**Deliverable**: `server.py` starts, all tools callable, return valid Pydantic-typed responses.

### Scaffold
- [x] `contracts.py` — all MCP response types + shared sub-types
- [x] `server.py` — MCP entrypoint, tools registered
- [x] `requirements.txt`

### Handlers (backed by mocks)
- [x] `handlers/decision_status.py` — returns `DecisionStatusResponse`
- [x] `handlers/search_decisions.py` — returns `SearchDecisionsResponse`
- [x] `handlers/detect_drift.py` — returns `DetectDriftResponse`
- [x] `handlers/link_commit.py` — returns `LinkCommitResponse`

### Adapters
- [x] `adapters/ledger.py` — mock mode (now replaced with real)
- [x] `adapters/code_locator.py` — mock mode (now replaced with real)

### Mocks — DELETED
- [x] `mocks/decision_ledger.py` — deleted, replaced by `ledger/adapter.py::SurrealDBLedgerAdapter`
- [x] `mocks/code_locator.py` — deleted, replaced by `RealCodeLocatorAdapter`

---

## Phase 1: Wire Real Code Locator / CocoIndex — DONE

**Owner**: Silong (code-locator) + Jin (adapter wiring)

### Architecture Decision: Host Model Orchestrates, MCP Retrieves

The MCP server calls no nested LLM and performs no code search. `pilot/mcp` owns deterministic primitives in `code_locator/`:

- `validate_symbols(candidates)` — rapidfuzz + SQLite-backed symbol validation
- `get_neighbors(symbol_id)` — structural expansion from the local index
- `extract_symbols(file_path)` — tree-sitter symbol extraction (no index needed)

Code search is caller-owned: Claude Code / Cursor / etc. use their native Grep/Read/Glob tools and hand file paths + symbols to the server via `bicameral.bind` and the `file_paths` field on `bicameral.preflight`.

### Changes
- [x] `adapters/code_locator.py` — `RealCodeLocatorAdapter` with lazy init
- [x] Extract deterministic tool implementations into `pilot/mcp/code_locator/`
- [x] MCP tool handlers for `validate_symbols`, `get_neighbors`, `extract_symbols`
- [x] Removed litellm entirely — no LLM dependency in MCP server
- [x] v0.6.4: removed `search_code` tool + BM25/RRF/vector retrieval stack

### Verification
- [x] Running `validate_symbols`/`get_neighbors` requires no provider credentials
- [x] No litellm import or dependency anywhere in `pilot/mcp/`
- [x] Anti-hallucination guarantees: every returned file/symbol comes from indexed repo state

---

## Phase 2: Wire Decision Ledger (SurrealDB) — DONE

**Owner**: Jin

### Changes
- [x] `adapters/ledger.py` — `SurrealDBLedgerAdapter` singleton (wraps `ledger/adapter.py`)
- [x] `handlers/decision_status.py` — queries real graph
- [x] `handlers/search_decisions.py` — SurrealDB FTS over decision descriptions + graph walk
- [x] `handlers/detect_drift.py` — reverse traversal via `touches` edge + content-hash comparison
- [x] `handlers/link_commit.py` — real idempotent commit ingestion
- [x] `handlers/ingest.py` — payload ingestion with source cursor tracking
- [x] Deleted mock files, adapters always return real implementations

---

## Phase 3: Integration + Hardening — IN PROGRESS

### Done
- [x] Zero active mocks
- [x] Full E2E verified
- [x] GitHub Actions CI replaces pre-push git hook
- [x] Performance: `search_decisions` < 2s, `detect_drift` < 1s on 100+ decisions — measured by V1 A1 (`tests/bench_drift.py`) at p95 = 10.4 ms / 15.5 ms, 55–185× under target

### Remaining
- [ ] LLM drift judge: wire `claude-haiku-4-5` for changed-region comparison in `detect_drift` (V2 — `docs/desync-optimization.md` §8 C2)
- [ ] All 4 tools demoed live in Claude Code (MCP connected)

## Desync Optimization V1 — DONE (read-path advisory + measurement)

Plan: `docs/desync-optimization-v1-plan.md`. V2 design target with full
Codex-review history: `docs/desync-optimization.md`. V1 introduces zero
new mutating capabilities.

- [x] A1 — `tests/bench_drift.py` benchmark harness; `test-results/bench/drift_baseline.json` artifact
- [x] A2-light — `handlers/sync_middleware.repo_write_barrier(ctx)` (per-repo `asyncio.Lock`); `handle_bind` wrapped
- [x] A3 — `contracts.SyncMetrics` + `sync_metrics` field on Search/Preflight/History/Bind responses
- [x] B1 — `ledger/ast_diff.is_cosmetic_change(before, after, lang)` (strict tree-sitter whitelist, 21 tests)
- [x] B2 — `DriftEntry.cosmetic_hint` advisory + `_enrich_with_cosmetic_hints` helper in `handle_detect_drift`
- [x] D1 — `original_lines` field on `symbol_disappeared` grounding checks; **`_build_verification_instruction` split** so relocation cases never get the unsafe `bicameral.bind` CTA
- [x] F1 — `tests/test_desync_scenarios.py` canonical 13-scenario regression matrix (12 pass + 1 V2 xfail)
- [x] Incidental fix — empty `decision_id` on ungrounded `pending_grounding_checks` (`ledger/adapter.py:475`)

---

## Mock → Real Swap Summary

| Mock | Replaced by | Phase | Status |
|------|------------|-------|--------|
| `mocks/code_locator.py` | `RealCodeLocatorAdapter` in `adapters/code_locator.py` | Phase 1 | **Deleted** |
| `mocks/decision_ledger.py` | `SurrealDBLedgerAdapter` in `ledger/adapter.py` | Phase 2 | **Deleted** |
