# Architecture Plan

## Risk Grade: L2

### Risk Assessment
- [x] Modifies existing APIs → **L2** (handlers extend MCP tool surface;
      schema migrates user data)
- [ ] Contains security/auth logic → not L3 (no auth/authorization layer
      exists; data is user-local)
- [ ] UI-only changes → not L1 (logic, schema, and migration changes
      are routine)

L2 routing implies `/qor-audit` is **mandatory before implementation** for
all feature work touching the ledger, handlers, retrieval, or schema.

## File Tree (The Contract)

```text
bicameral-mcp/                  (repo root, flat layout — no `bicameral/` package)
├── server.py                   MCP entrypoint; tool registry; stdio loop
├── context.py                  request-scoped BicameralContext (frozen dataclass)
├── contracts.py                MCP boundary types (Pydantic) — never leak internal types
├── tool_definitions.py         MCP tool schema declarations
├── code_locator_runtime.py     index lifecycle; HEAD/ref resolution
├── ports.py / events.py /      cross-cutting infra (telemetry, IPC)
│   telemetry.py
├── adapters/                   factory layer (`get_*()` singletons)
│   ├── ledger.py               returns SurrealDBLedgerAdapter
│   └── code_locator.py         returns RealCodeLocatorAdapter
├── handlers/                   one file per MCP tool — thin orchestration
│   ├── bind.py / ingest.py / link_commit.py / detect_drift.py
│   ├── decision_status.py / search_decisions.py / preflight.py
│   ├── ratify.py / resolve_compliance.py / resolve_collision.py
│   ├── update.py / reset.py / history.py / analysis.py
│   ├── action_hints.py / gap_judge.py / sync_middleware.py
│   └── ingest_grounding.py
├── ledger/                     SurrealDB adapter + queries + schema
│   ├── adapter.py              SurrealDBLedgerAdapter (singleton via adapters/)
│   ├── client.py               async SDK wrapper; result normalization
│   ├── queries.py              all SurrealQL helpers (1310 LOC monolith)
│   ├── schema.py               canonical TABLE/INDEX/FIELD definitions + migrations
│   ├── status.py               content_hash, derive_status, git plumbing
│   ├── drift.py / ast_diff.py / canonical.py
│   ├── commit_sync.py / payload_ingest.py
│   └── queries_read.py / queries_sync.py / queries_write.py
├── code_locator/               deterministic retrieval (BM25 + tree-sitter + RRF)
│   ├── config.py / models.py
│   ├── indexing/               symbol_extractor, graph_builder, sqlite_store, cocoindex_*
│   ├── retrieval/              bm25s_client, sqlite_vec_client
│   ├── fusion/                 RRF
│   └── tools/                  validate_symbols, search_code, get_neighbors
├── dashboard/                  optional web UI for ledger viewing
├── skills/                     Claude skill definitions (.md)
├── docs/                       this directory; project DNA + design docs
├── tests/                      pytest suite (markers: phase1/phase2/phase3/alpha_flow/bench)
└── .github/workflows/          CI (test-mcp-regression.yml etc.)
```

## Interface Contracts

### MCP boundary (server.py → handlers/)

- **Input**: tool name + JSON arguments (validated against `tool_definitions.py`)
- **Output**: Pydantic model from `contracts.py` (e.g. `BindResponse`,
  `DetectDriftResponse`)
- **Side Effects**: ledger writes, dashboard notifications; no network I/O
  in the deterministic path

### Ledger adapter (handlers/ → ledger/adapter.py)

- **Input**: domain primitives (`decision_id`, `file_path`, line range, etc.)
- **Output**: dicts of domain values; no SDK types leak across the boundary
- **Side Effects**: SurrealDB writes (idempotent via UNIQUE indexes);
  schema init/migration on connect

### Code locator (handlers/ → code_locator/)

- **Input**: free-text query, candidate symbols, or file paths
- **Output**: ranked symbol/region candidates with provenance
- **Side Effects**: builds local sqlite index on first use; cached
  per-repo

## Data Flow

```text
caller LLM (Claude/IDE)
   ↓ MCP stdio
server.py → tool_definitions → handlers/<tool>.py
   ↓ ctx (BicameralContext, frozen)
adapters/ → ledger.adapter.SurrealDBLedgerAdapter / RealCodeLocatorAdapter
   ↓ SurrealQL / sqlite / git
embedded SurrealDB (surrealkv://) + tree-sitter + bm25s
   ↓ derived status (query-time, not stored)
contracts.py (Pydantic) → MCP TextContent → caller LLM
```

Status derivation is **always derived from intent + git ref at query
time** (not stale stored state). `content_hash` is the integrity fingerprint
linking decision-tier bindings to the retrieval-tier code regions.

## Dependencies

| Package | Justification | Vanilla Alternative |
|---|---|---|
| `mcp>=1.0.0` | MCP server protocol | no |
| `surrealdb>=1.0.0` | embedded graph DB (the whole ledger) | no — graph traversal + FTS in one engine is the design |
| `tree-sitter` + parsers | deterministic symbol extraction | regex (rejected — too brittle for multi-language symbol kinds) |
| `bm25s>=0.2.0` | local BM25 index | sqlite FTS (slower; lacks BM25 tuning) |
| `rapidfuzz>=3.6.0` | fuzzy symbol name match | python `difflib` (slower) |
| `sqlite-vec>=0.1.0` | optional vector cache | none (vectors are opt-in) |
| `pydantic>=2.0.0` | MCP boundary contracts | dataclasses (rejected — JSON serialization story) |
| `cocoindex>=0.3.36` | indexing pipeline orchestration | hand-rolled (rejected — dataflow ergonomics) |
| `pyyaml`, `python-dotenv` | config loading | json (yes, but `.bicameral/config.yaml` is human-edited) |

## Section 4 Razor Pre-Check

- [x] All planned **new** functions ≤ 40 lines (codegenome scope)
- [x] All planned **new** files ≤ 250 lines (codegenome scope)
- [x] No planned new nesting > 3 levels
- [ ] **Existing** files: `ledger/queries.py` is 1310 LOC monolith — known
      tech debt, not split as part of #59 (out of scope)
- [ ] **Existing** files: `ledger/adapter.py`, `contracts.py` are >600 LOC
      each — same status

The Section 4 razor is enforced for all *new* code; legacy oversize files
are tracked in BACKLOG.md (see `[B1] split queries.py`) but are not
required-to-fix gates for incremental feature PRs.

---
*Blueprint sealed. Awaiting GATE tribunal.*
