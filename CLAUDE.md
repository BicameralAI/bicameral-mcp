# MCP Server — Claude Agent Instructions

## Canonical Skill Source

`pilot/mcp/skills/` is the **single canonical location** for all skill files in this project. Do not edit `.claude/skills/bicameral-*/SKILL.md` copies — they are stale duplicates and should be deleted. When a skill file changes, commit only the `pilot/mcp/skills/` version.

## Tool Changes Require Skill Changes (Mandatory)

Any change to an MCP tool's behavior — new fields in a response, new status values,
changed defaults, new tool calls, deprecated params — **must ship with a matching
update to the relevant `skills/*/SKILL.md`** in the same commit.

This is not optional. A tool change with no skill update is incomplete. The skill
is the contract between the server and the agent layer; breaking it silently is
worse than a compile error because it fails at runtime in production sessions.

**Checklist before marking a tool PR complete:**
- [ ] Did any response field change shape or gain a new value? → Update skill rendering section
- [ ] Did any default behavior change? → Update skill's "Steps" or "After" section
- [ ] Did a new tool get added? → Create `skills/<tool-name>/SKILL.md`
- [ ] Did a status literal gain a new value (e.g. `"proposal"`)? → Update every skill that renders status

## Auto-Tick Rule

After completing **any** implementation work in this directory:
1. Open `TODO.md` — tick every item that is now done under **Engineering Progress**
2. Open `PLAN.md` — tick every phase item that is now done
3. If you replaced a mock with a real implementation, update `mocks/README.md`:
   - Move the entry from **Active Mocks** to **Replaced Mocks**
   - Record the date and what replaced it

Never mark something complete until the code is actually written and verified to import/run.

## Directory Layout

```
pilot/mcp/
├── CLAUDE.md          ← you are here
├── PLAN.md            ← phased implementation plan (tick as you go)
├── TODO.md            ← hackathon task tracking + engineering progress
├── server.py          ← MCP server entrypoint (13 tools: 10 ledger + 3 code locator primitives)
├── contracts.py       ← MCP response types (Pydantic)
├── code_locator_runtime.py ← index lifecycle management
├── adapters/          ← thin adapter layer
│   ├── ledger.py      ← returns SurrealDBLedgerAdapter (singleton)
│   └── code_locator.py← returns RealCodeLocatorAdapter
├── handlers/          ← one file per MCP tool
│   ├── decision_status.py
│   ├── search_decisions.py
│   ├── detect_drift.py
│   ├── link_commit.py
│   └── ingest.py
├── ledger/            ← real SurrealDB adapter + queries
│   ├── adapter.py     ← SurrealDBLedgerAdapter
│   ├── client.py
│   ├── queries.py
│   ├── schema.py      ← canonical source for all table/index definitions
│   └── status.py
├── code_locator/      ← symbol index + deterministic primitives
│   ├── config.py
│   ├── models.py
│   ├── indexing/      ← tree-sitter symbol extraction, graph building, sqlite store
│   └── tools/         ← validate_symbols, get_neighbors (no code search —
│                         callers use Grep/Read for retrieval)
├── mocks/             ← retired (README.md tracks history)
│   └── README.md
└── tests/
    ├── conftest.py
    ├── fixtures/
    ├── test_phase1_code_locator.py
    ├── test_phase2_ledger.py
    ├── test_phase3_integration.py
    └── ... (stress tests, smoke tests)
```

## SurrealDB Version — PINNED TO v2 (embedded via Python SDK)

**Critical for code generation**: All SurrealQL in this project targets SurrealDB **2.x embedded**
via the Python SDK (`surrealdb>=1.0.0`). Do NOT use v3 syntax.

| Feature | ✅ v2 (use this) | ❌ v3 (do NOT use) |
|---|---|---|
| Full-text search index | `SEARCH ANALYZER` | `FULLTEXT ANALYZER` |
| Connection URL | `surrealkv://path` or `memory://` | standalone server |
| Auth (embedded) | no signin needed for `memory://` | `signin()` required |

**Reference**: `ledger/schema.py` is the canonical source for all table/index definitions.
Ground any new SurrealQL against the patterns there — the `@0@` operator, `RELATE`, and
graph traversal (`->table->`) all behave identically to v3 except for FTS index syntax.

**Known v2 quirks** (documented so you don't re-discover them):
- `search::score(0)` always returns `0.0` in embedded mode — use presence in results as match signal
- `AS` alias is NOT supported inside graph traversal field selectors (e.g. `->code_region.{name AS n}`)
- `ORDER BY` requires the field to be explicitly in the SELECT list when other fields use function transforms
- `INFO FOR TABLE` returns empty in embedded mode (use schema.py as ground truth instead)

## Env Vars

| Var | Default | Effect |
|-----|---------|--------|
| `SURREAL_URL` | `surrealkv://~/.bicameral/ledger.db` | SurrealDB URL. Use `memory://` for tests (no persistence). |
| `REPO_PATH` | `.` | Path to the repo being analyzed |

## CI

Tests run via GitHub Actions on PRs to `main` (see `.github/workflows/test-mcp-regression.yml`).
All phases use real adapters with `SURREAL_URL=memory://` (embedded, in-process).
Results are uploaded as artifacts (JUnit XML + HTML reports) for qualitative review.


<claude-mem-context>
# Recent Activity

<!-- This section is auto-generated by claude-mem. Edit content outside the tags. -->

*No recent activity*
</claude-mem-context>