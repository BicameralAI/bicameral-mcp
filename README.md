# Bicameral MCP — Decision Ledger for Your Codebase

Every software team makes hundreds of verbal decisions per week — in meetings, PRDs, Slack threads, and huddles. None of those decisions are linked to the code that implements (or fails to implement) them.

**Bicameral MCP** is a local MCP server that ingests meeting transcripts and PRDs, builds a structured graph of decisions mapped to code symbols, and continuously tracks whether those decisions are reflected, drifting, or lost as the codebase evolves.

> **One-liner**: A provenance-aware decision layer for your codebase — paste a transcript, get a living map of what was decided and what was actually built.

---

## The Problem: 5 SDLC Friction Points

| Smell | What Happens | Fix |
|-------|-------------|-----|
| **CONSTRAINT_LOST** | A rate limit or compliance rule surfaces mid-sprint instead of at design time | `bicameral.search` — pre-flight context before coding |
| **CONTEXT_SCATTERED** | The "why" behind a decision is split across Slack, Notion, and someone's memory | `bicameral.ingest` — normalizes intent from any source into a unified graph |
| **DECISION_UNDOCUMENTED** | A verbal "let's do X" never lands in a ticket or ADR | `bicameral.status` — tracks what was decided vs what was built |
| **REPEATED_EXPLANATION** | Same context tax paid twice — once to design, once to engineering | `bicameral.search` — retrieves full decision provenance |
| **TRIBAL_KNOWLEDGE** | Only one person knows why the system works the way it does | `bicameral.drift` — surfaces institutional memory tied to code |

**Bicameral's value is drift detection** — knowing that a decision made three weeks ago is now inconsistent with what actually shipped, or that a decision made today is inconsistent with the codebase reality. 

---

## Quickstart

### One-command setup

```bash
pipx install bicameral-mcp
bicameral-mcp setup
```

> **Note:** `bicameral-mcp` bundles ML dependencies (`sentence-transformers`, `cocoindex`) for semantic code search. These add ~1GB on first install but are required for auto-grounding. `pipx` gives a persistent install — `pipx upgrade bicameral-mcp` when you want a new version.

This launches an interactive wizard that:
1. Detects your repo (from cwd or prompts you)
2. Installs the MCP config into Claude Code using your `pipx` binary path

That's it. The server builds its code index on first tool call.

### Manual config

Run from your repo root:

```bash
# Install first
pipx install bicameral-mcp

# Then add to Claude Code
claude mcp add-json bicameral --scope local '{
  "command": "bicameral-mcp",
  "args": [],
  "env": {
    "REPO_PATH": "/path/to/your/repo",
    "SURREAL_URL": "surrealkv:///path/to/your/repo/.bicameral/ledger.db"
  }
}'
```

### Local development

```bash
pip install -e ".[test]"
bicameral-mcp setup           # interactive config
bicameral-mcp --smoke-test    # verify tools
bicameral-mcp                 # start MCP server (stdio)
```

No LLM provider credentials needed — all retrieval is deterministic.

---

## 5 MCP Tools

| Tool | Purpose | Auto-triggers |
|------|---------|---------------|
| `bicameral.ingest` | Ingest a normalized source payload (transcript, PRD, Slack export) and advance the source cursor | — |
| `bicameral.status` | Surface implementation status of all tracked decisions (reflected / drifted / pending / ungrounded) | — |
| `bicameral.search` | Pre-flight: find past decisions relevant to a feature before writing code | `link_commit(HEAD)` |
| `bicameral.drift` | Code review: surface decisions that touch symbols in a file, flagging divergence | `link_commit(HEAD)` |
| `bicameral.link_commit` | Sync a commit into the ledger — updates content hashes, re-evaluates drift | — |

---

## How the Tools Compose

### Pre-flight (before coding)
```
bicameral.search("add rate limiting") → surfaces prior constraints and code regions
```

### Code review (before merging)
```
bicameral.drift("payments/processor.py") → surfaces decisions touching this file
bicameral.status(filter="drifted") → full drift report
```

### Ingestion (after a meeting)
```
bicameral.ingest(transcript_payload) → extracts intents, maps to code
bicameral.link_commit("HEAD") → syncs latest commit state
bicameral.status(since="2026-03-20") → shows what's reflected vs pending
```

---

## Status Derivation

Decision status is a pure function computed at query time — never stored:

| Condition | Status | Meaning |
|-----------|--------|---------|
| No `code_region` mapped | **ungrounded** | Intent captured but no matching code found |
| Symbol absent at git ref | **pending** | Code not yet written |
| `content_hash` differs | **drifted** | Code changed since decision was recorded |
| `content_hash` matches | **reflected** | Code matches intent |

This makes Bicameral immune to rebase, squash, and cherry-pick — status is always re-derivable from `(intent, git_ref)`.

---

## Environment Variables

| Var | Default | Purpose |
|-----|---------|---------|
| `REPO_PATH` | `.` | Path to the repo being analyzed |
| `SURREAL_URL` | `surrealkv://~/.bicameral/ledger.db` | SurrealDB URL. Use `memory://` for tests. |

---

## Tests

```bash
pip install -e ".[test]"
pytest tests/ -v
```

| Phase | Tests | What |
|-------|-------|------|
| **Phase 1** | `test_phase1_code_locator.py` | Code locator tools against real indexed repo |
| **Phase 2** | `test_phase2_ledger.py` | SurrealDB ledger adapter with `memory://` |
| **Phase 3** | `test_phase3_integration.py` | Full E2E structured around 5 SDLC failure modes |

Phase 3 tests produce JSON artifacts (`test-results/e2e/`) with full tool responses and SurrealDB graph dumps for qualitative review.
