```
    ██████╗ ██╗ ██████╗ █████╗ ███╗   ███╗███████╗██████╗  █████╗ ██╗
    ██╔══██╗██║██╔════╝██╔══██╗████╗ ████║██╔════╝██╔══██╗██╔══██╗██║
    ██████╔╝██║██║     ███████║██╔████╔██║█████╗  ██████╔╝███████║██║
    ██╔══██╗██║██║     ██╔══██║██║╚██╔╝██║██╔══╝  ██╔══██╗██╔══██║██║
    ██████╔╝██║╚██████╗██║  ██║██║ ╚═╝ ██║███████╗██║  ██║██║  ██║███████╗
    ╚═════╝ ╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝

        ┌────────────────┐                    ┌────────────────┐
        │   DECISIONS    │  ◀── drift ──▶    │      CODE      │
        │   what was     │    detection       │  what actually │
        │     said       │                    │     shipped    │
        └────────┬───────┘                    └────────┬───────┘
                 │           ┌──────────┐              │
                 └──────────▶│  ledger  │◀─────────────┘
                             └──────────┘
                         local · deterministic
```

# Bicameral MCP

[![PyPI version](https://img.shields.io/pypi/v/bicameral-mcp)](https://pypi.org/project/bicameral-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/bicameral-mcp)](https://pypi.org/project/bicameral-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![CI](https://img.shields.io/github/actions/workflow/status/BicameralAI/bicameral-mcp/test-mcp-regression.yml?branch=main&label=tests)](https://github.com/BicameralAI/bicameral-mcp/actions)

**A provenance-aware decision layer for your codebase** -- paste a transcript, get a living map of what was decided and what was actually built.

Bicameral MCP is a local-first [Model Context Protocol](https://spec.modelcontextprotocol.io/) server that ingests meeting transcripts, PRDs, and design documents, builds a structured graph of decisions mapped to code symbols, and continuously tracks whether those decisions are reflected, drifting, or lost as the codebase evolves. No data leaves your machine. No LLM required -- all retrieval is deterministic. No API keys needed.

---

## Table of Contents

- [The Problem](#the-problem)
- [Collaboration Modes](#collaboration-modes)
- [Tool Composition](#tool-composition)
- [How It Works](#how-it-works)
- [Architecture](#architecture)
- [Quickstart](#quickstart)
- [MCP Tools Reference](#mcp-tools-reference)
- [Testing](#testing)
- [Configuration](#configuration)
- [Contributing](#contributing)
- [License](#license)

---

## The Problem

Every software team makes hundreds of verbal decisions per week -- in meetings, PRDs, Slack threads, and huddles. None of those decisions are linked to the code that implements them. This disconnect creates five specific SDLC friction points:

| # | Smell | What Happens | Bicameral Fix |
|---|-------|-------------|---------------|
| 1 | **CONSTRAINT_LOST** | A rate limit or compliance rule surfaces mid-sprint instead of at design time | `bicameral.search` -- pre-flight context before coding |
| 2 | **CONTEXT_SCATTERED** | The "why" behind a decision is split across Slack, Notion, and someone's memory | `bicameral.ingest` -- normalizes intent from any source into a unified graph |
| 3 | **DECISION_UNDOCUMENTED** | A verbal "let's do X" never lands in a ticket or ADR | `bicameral.status` -- tracks what was decided vs. what was built |
| 4 | **REPEATED_EXPLANATION** | Same context tax paid twice -- once to design, once to engineering | `bicameral.search` -- retrieves full decision provenance on demand |
| 5 | **TRIBAL_KNOWLEDGE** | Only one person knows why the system works the way it does | `bicameral.drift` -- surfaces institutional memory tied to code |

Bicameral's core value is **drift detection** -- knowing that a decision made three weeks ago is now inconsistent with what actually shipped, or that a decision made today is inconsistent with the codebase reality.

---

## Collaboration Modes

Bicameral runs in one of two modes, set during `bicameral-mcp setup` or in `.bicameral/config.yaml`:

| | Solo (default) | Team |
|---|---|---|
| **Who** | Individual testing or evaluation | Any mix of roles -- devs, PMs, designers |
| **Data** | Local DB only | Local DB + git-committed event files |
| **Shared via** | Nothing -- fully isolated, zero impact on teammates | Normal `git push` / `git pull` |
| **Merge conflicts** | N/A | Zero -- per-user directories, append-only files |

**Solo mode** is ideal for trying Bicameral without affecting your team's workflow. All data stays in a gitignored local DB -- no event files, no commits, no side effects. Switch to team mode when you're ready to share.

**Team mode** enables cross-role collaboration through git. A PM ingests a PRD and sprint transcript; when a developer pulls, `bicameral.search` surfaces those decisions as coding context and `bicameral.status` shows what still needs implementation. The PM never touches the code; the developer never sits through the meeting. The decision graph is the handoff.

```
.bicameral/
├── events/              ← committed to git (shared decisions)
│   ├── pm@co.com/       ← PM's ingested PRDs and transcripts
│   └── dev@co.com/      ← developer's commit syncs
├── config.yaml          ← committed (mode: solo | team)
└── local/               ← gitignored (materialized state)
```

**Switching modes:** Set `mode: team` or `mode: solo` in `.bicameral/config.yaml`. No data migration needed.

---

## Tool Composition

The nine tools compose into three workflows that follow the natural lifecycle of a decision — **captured in a meeting, pulled as context during coding, checked at review time.** Each workflow below uses the same running example (a checkout-flow sprint) so you can see a single decision move through the pipeline.

### 1. Ingestion — after a meeting

> **Scenario:** Your PM wraps a 30-minute sprint planning in `#product-planning`. The transcript contains three decisions. You paste it into Claude and say "ingest this."

```jsonc
// bicameral.ingest
{
  "source": "slack",
  "title": "Sprint 14 Planning — 2026-03-12",
  "decisions": [
    { "title": "Apply 10% discount on orders over $100",
      "description": "Marketing confirmed at offsite. No upper bound." },
    { "title": "Cache user sessions in Redis, not local memory",
      "description": "Arch review: local memory breaks horizontal scaling." },
    { "title": "Rate-limit checkout to 100 req/min per user",
      "description": "Legal/compliance ask. Not yet built." }
  ]
}
```

**Outcome.** The discount rule and the Redis session decision anchor to real symbols (`pricing/discount.py:DiscountService.calculate`, `auth/session_store.py:SessionStore.put`) and are born `reflected`. Auto-grounding can't find code for the rate-limit rule — because it hasn't been written yet — so it lands as `ungrounded`. The ledger now knows a decision exists with no corresponding code, and the next `bicameral.status` call will show exactly that.

---

### 2. Pre-flight — before writing new code

> **Scenario:** A dev picks up the ticket "add rate limiting to checkout." Before writing a single line, they ask Claude for context.

```jsonc
// bicameral.search
{ "query": "rate limit checkout", "max_results": 5 }
```

**Outcome.** Before writing any code, the dev sees the prior rate-limit decision *with its compliance rationale*, learns that it's still `ungrounded` (so they're the first implementer), and discovers an adjacent `pricing/discount.py:DiscountService.calculate` region their new code will need to coexist with. No re-litigating a decided rule, no Slack archaeology, no ambushing the PM in standup tomorrow.

---

### 3. Code review — before merging

> **Scenario:** Three weeks later, a different dev opens PR #241 with a 50-line diff touching `pricing/discount.py`. Reviewer asks Claude "any drift in this file?"

```jsonc
// bicameral.drift
{ "file_path": "pricing/discount.py", "use_working_tree": false }
```

**Outcome.** The reviewer learns that `DiscountService.calculate:42-67` has drifted from the Sprint 14 Planning decision — threshold raised $100 → $500, rate lowered 10% → 5%. Either the change is intentional, in which case a new decision must be ingested before merge, or it's accidental and gets reverted. The conversation happens at PR time, not three sprints later in an incident post-mortem.

---

## How It Works

### Status Derivation Model

Decision status is a **pure function** computed at query time -- never stored. This is the key differentiator: because status is derived from `(intent, git_ref)`, Bicameral is immune to rebase, squash, and cherry-pick. There is no stale state to reconcile.

| Condition | Status | Meaning |
|-----------|--------|---------|
| No `code_region` mapped | **ungrounded** | Intent captured, but no matching code found |
| Symbol absent at git ref | **pending** | Code not yet written |
| `content_hash` differs | **drifted** | Code changed since the decision was recorded |
| `content_hash` matches | **reflected** | Code matches intent |

### Auto-Grounding

When decisions are ingested, Bicameral automatically attempts to anchor them to code:

1. **BM25 file-level search** -- ranks candidate files by textual relevance to the decision description
2. **Symbol expansion** -- extracts all symbols from top-ranked files via tree-sitter
3. **Fuzzy token matching** -- matches decision terminology against the symbol index using rapidfuzz

This is a deterministic, two-stage retrieval pipeline. No embeddings, no LLM calls.

---

## Architecture

Bicameral is composed of three layers:

<details>
<summary><strong>Layer diagram and data flow</strong></summary>

```
                        MCP Client (Claude Code, etc.)
                                    |
                              stdio transport
                                    |
                    +-------------------------------+
                    |        MCP Server Layer        |
                    |          (server.py)           |
                    |   9 tools, stdio transport,    |
                    |   Pydantic response contracts  |
                    +-------+---------------+-------+
                            |               |
              +-------------+               +-------------+
              |                                           |
   +----------v-----------+                 +-------------v-----------+
   |   Decision Ledger    |                 |      Code Locator       |
   |      (ledger/)       |                 |    (code_locator/)      |
   |                      |                 |                         |
   |  SurrealDB embedded  |                 |  tree-sitter parsing    |
   |  Graph: intent -->   |                 |  BM25 text search       |
   |    maps_to -->       |                 |  RRF fusion ranking     |
   |    symbol -->        |                 |  Structural graph       |
   |    implements -->    |                 |  traversal              |
   |    code_region       |                 |                         |
   +----------------------+                 +-------------------------+
```

**Data flow:**

| Operation | Flow |
|-----------|------|
| **Ingest** | Transcript/PRD --> `bicameral.ingest` --> SurrealDB graph (intents, symbols, code_regions, edges) |
| **Sync** | Code change --> `bicameral.link_commit` --> content hash update, drift re-evaluation |
| **Query** | `bicameral.status` / `drift` / `search` --> derives status from `(intent, git_ref)` at query time |

**Supported languages** (tree-sitter grammars): Python, JavaScript, JSX, TypeScript, TSX, Java, Go, Rust, C#

</details>

### Core Technologies

| Component | Technology | Role |
|-----------|-----------|------|
| Decision store | SurrealDB v2 (embedded, in-process) | Graph storage for intents, symbols, code regions, and edges |
| Symbol extraction | tree-sitter (9 language grammars) | AST-level function/class extraction |
| Text search | BM25 via bm25s | File and symbol ranking |
| Fuzzy matching | rapidfuzz | Token-level matching for auto-grounding |
| Response types | Pydantic v2 | Strict MCP response contracts |
| Transport | MCP protocol (stdio) | IDE/agent integration |

---

## Quickstart

### One-command setup

```bash
pipx install bicameral-mcp
bicameral-mcp setup
```

This launches an interactive wizard that:
1. Detects your repo (from cwd or prompts you)
2. Installs the MCP config into Claude Code using your `pipx` binary path

That's it. The server builds its code index on first tool call.

### Manual config

Run from your repo root:

```bash
# Install
pipx install bicameral-mcp

# Add to Claude Code
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
# Option A: pipx editable install (puts bicameral-mcp on PATH, uses local source)
pipx install -e . --force

# Option B: pip editable install (venv-local, includes test deps)
pip install -e ".[test]"
```

After either option:

```bash
bicameral-mcp setup           # interactive config
bicameral-mcp --smoke-test    # verify all 9 tools register correctly
bicameral-mcp                 # start MCP server (stdio)
```

### Verify installation

```bash
bicameral-mcp --smoke-test
```

Expected output:
```
bicameral-mcp 0.2.13 smoke test passed
bicameral.status
bicameral.search
bicameral.drift
bicameral.link_commit
bicameral.ingest
validate_symbols
search_code
get_neighbors
extract_symbols
```

No LLM provider credentials needed -- all retrieval is deterministic.

---

## MCP Tools Reference

### Ledger Tools (5)

| Tool | Purpose |
|------|---------|
| `bicameral.status` | Surface implementation status of all tracked decisions (reflected / drifted / pending / ungrounded) |
| `bicameral.search` | Pre-flight: find past decisions relevant to a feature before writing code. Auto-syncs to HEAD. |
| `bicameral.drift` | Code review: surface decisions that touch symbols in a file, flagging divergence |
| `bicameral.link_commit` | Sync a commit into the ledger -- updates content hashes, re-evaluates drift. Idempotent. |
| `bicameral.ingest` | Ingest a normalized source payload (transcript, PRD, Slack export) and advance the source cursor |

### Code Locator Tools (4)

| Tool | Purpose |
|------|---------|
| `validate_symbols` | Fuzzy-match candidate symbol names against the code index. Returns confidence scores and symbol IDs. |
| `search_code` | BM25 text search + structural graph traversal with RRF fusion. Optionally seed with symbol IDs. |
| `get_neighbors` | 1-hop structural graph traversal around a symbol (callers, callees, imports, inheritance). |
| `extract_symbols` | Tree-sitter symbol extraction from a source file. No index required. |

<details>
<summary><strong>Full tool input schemas</strong></summary>

#### bicameral.status

```json
{
  "type": "object",
  "properties": {
    "filter": {
      "type": "string",
      "enum": ["all", "drifted", "pending", "reflected", "ungrounded"],
      "default": "all",
      "description": "Filter decisions by status"
    },
    "since": {
      "type": "string",
      "description": "ISO date — only decisions ingested after this date"
    },
    "ref": {
      "type": "string",
      "default": "HEAD",
      "description": "Git ref to evaluate against"
    }
  }
}
```

#### bicameral.search

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "Natural language description — e.g. 'add retry with backoff'"
    },
    "max_results": {
      "type": "integer",
      "default": 10
    },
    "min_confidence": {
      "type": "number",
      "default": 0.5,
      "description": "Minimum BM25 confidence score (0-1)"
    }
  },
  "required": ["query"]
}
```

#### bicameral.drift

```json
{
  "type": "object",
  "properties": {
    "file_path": {
      "type": "string",
      "description": "File path relative to repo root"
    },
    "use_working_tree": {
      "type": "boolean",
      "default": true,
      "description": "True = compare against disk (pre-commit), False = compare against HEAD"
    }
  },
  "required": ["file_path"]
}
```

#### bicameral.link_commit

```json
{
  "type": "object",
  "properties": {
    "commit_hash": {
      "type": "string",
      "default": "HEAD",
      "description": "Git commit hash or ref to sync (default: HEAD)"
    }
  }
}
```

#### bicameral.ingest

```json
{
  "type": "object",
  "properties": {
    "payload": {
      "type": "object",
      "description": "Normalized ingest payload matching the internal code-locator handoff shape"
    },
    "source_scope": {
      "type": "string",
      "default": "default",
      "description": "Source stream identifier, e.g. Slack channel or Notion database"
    },
    "cursor": {
      "type": "string",
      "description": "Optional upstream checkpoint (timestamp, event id, updated_at)"
    }
  },
  "required": ["payload"]
}
```

#### validate_symbols

```json
{
  "type": "object",
  "properties": {
    "candidates": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Symbol name hypotheses to validate (e.g. ['CheckoutController', 'processOrder'])"
    }
  },
  "required": ["candidates"]
}
```

#### search_code

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "Text search query (e.g. 'checkout rate limit middleware')"
    },
    "symbol_ids": {
      "type": "array",
      "items": { "type": "integer" },
      "description": "Symbol IDs from validate_symbols to use as graph traversal seeds"
    }
  },
  "required": ["query"]
}
```

#### get_neighbors

```json
{
  "type": "object",
  "properties": {
    "symbol_id": {
      "type": "integer",
      "description": "Symbol ID from validate_symbols results"
    }
  },
  "required": ["symbol_id"]
}
```

#### extract_symbols

```json
{
  "type": "object",
  "properties": {
    "file_path": {
      "type": "string",
      "description": "Absolute or repo-relative path to the source file"
    }
  },
  "required": ["file_path"]
}
```

</details>

---

## Testing

Bicameral has 42 test files organized into three phases, all using real adapters with `SURREAL_URL=memory://` (embedded, in-process SurrealDB -- no external services required).

```bash
pip install -e ".[test]"
pytest tests/ -v
```

| Phase | Entry Point | Scope |
|-------|-------------|-------|
| **Phase 1** | `test_phase1_code_locator.py` | Code locator tools against a real indexed repository |
| **Phase 2** | `test_phase2_ledger.py` | SurrealDB ledger adapter with `memory://` -- CRUD, graph traversal, cursor management |
| **Phase 3** | `test_phase3_integration.py` | Full end-to-end: structured around the 5 SDLC failure modes |

Additional test suites cover adversarial inputs (`test_stress_adversarial.py`), failure modes (`test_stress_failure_modes.py`), grounding state machine gaps (`test_grounding_state_machine_gaps.py`), and server smoke tests (`test_server_smoke.py`).

Phase 3 tests produce JSON artifacts (`test-results/e2e/`) with full tool responses and SurrealDB graph dumps for qualitative review. CI runs via GitHub Actions on PRs to `main`, with JUnit XML and HTML reports uploaded as artifacts.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `REPO_PATH` | `.` | Path to the repository being analyzed |
| `SURREAL_URL` | `surrealkv://~/.bicameral/ledger.db` | SurrealDB connection URL. Use `memory://` for tests (no persistence). |
| `CODE_LOCATOR_SQLITE_DB` | *(auto)* | Optional override for the local code index database path |

All data is stored locally. The embedded SurrealDB instance runs in-process -- no separate database server to manage.

---

## Contributing

Contributions are welcome. To get started:

```bash
git clone https://github.com/BicameralAI/bicameral-mcp.git
cd bicameral-mcp
pip install -e ".[test]"
pytest tests/ -v
```

Please open an issue before submitting large changes.

---

## License

MIT
