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
- [ ] Performance benchmarks
- [ ] LLM drift judge

---

## Mock Registry

All mocks deleted. See `mocks/README.md` for history.
