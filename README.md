```
  ▸ BICAMERAL
  ┌───────────────────────────────────────────────┐
  │  what your team decided  ↔  what the AI built │
  └───────────────────────────────────────────────┘
```

# Bicameral MCP

[![PyPI version](https://img.shields.io/pypi/v/bicameral-mcp)](https://pypi.org/project/bicameral-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/bicameral-mcp)](https://pypi.org/project/bicameral-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![CI](https://img.shields.io/github/actions/workflow/status/BicameralAI/bicameral-mcp/test-mcp-regression.yml?branch=main&label=tests)](https://github.com/BicameralAI/bicameral-mcp/actions)

Bicameral eliminates redundant rework in the software development lifecycle (SDLC) by building the *compliance layer* between product decisions and code output.

Bicameral is a local-first [MCP server](https://spec.modelcontextprotocol.io/) that ingests your meeting transcripts, PRDs, and Slack threads, maps every decision to the code that implements it, and automatically surfaces alignment gaps — before they become bugs.

---

## The Problem

Engineering teams make hundreds of product decisions per week. A tiny fraction end up in tickets. None are linked to the code that implements them.

When you build with an AI coding assistant, this disconnect accelerates:

- The agent has no memory of the sprint planning where you decided on the rate limit
- It implements checkout without knowing the compliance rule from last week's Slack thread
- By the time someone notices, the gap has compounded across three PRs

**Bicameral solves spec-alignment friction.** It acts as a persistent, auditable memory layer between your product decisions and your codebase — so your AI agent always has the right context before writing a line of code.

```
  meeting transcript       PRD / Slack thread       inline answer
         │                        │                       │
         └────────────────────────┼───────────────────────┘
                                  ▼
                          bicameral.ingest
                                  │
                    ┌─────────────▼──────────────┐
                    │       Decision Ledger        │
                    │   what was said  ↔  code    │
                    │  status: reflected | drifted │
                    │          | gap | ungrounded  │
                    └─────────────────────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
       preflight fires      dashboard shows      drift detected
     before you code        full picture        at review time
```

---

## How It Feels

**Before implementing a feature**, your agent runs `bicameral.preflight` and surfaces:

```
(bicameral surfaced — checking Stripe webhook context)

📌 3 prior decisions in scope:
  ✓ Idempotency via Redis SETNX with 24h TTL
    src/middleware/idempotency.ts:checkIdempotencyKey:42-67
    Source: Sprint 14 planning · Ian, 2026-03-12

  ⚠ DRIFTED: Trust Stripe event.created, not server time
    src/handlers/webhook.ts:processEvent:80-92
    Drift evidence: switched to Date.now() in PR #287

⚠ 1 unresolved open question:
  • "Should we deduplicate by event.id or (account_id, event.id)?"
    Source: Slack #payments 2026-03-20
```

**At any time**, the dashboard gives you the full picture:

![Bicameral Dashboard](assets/dashboard-preview.png)

---

## Quickstart

```bash
pipx install bicameral-mcp
bicameral-mcp setup
```

The setup wizard detects your repo, installs the MCP server config into Claude Code, adds a git hook that automatically syncs the ledger after every commit, and adds a session-end hook that captures any design decisions you stated mid-session but didn't explicitly ingest. Restart Claude Code and you're done.

Verify it works:

```bash
bicameral-mcp --smoke-test
```

### Don't have pipx?

**macOS**
```bash
brew install pipx
pipx ensurepath
```

**Linux**
```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

**Windows**
```powershell
python -m pip install --user pipx
python -m pipx ensurepath
```

Then restart your terminal and re-run the install command above.

---

## Core Concepts

### Decision Status

Every tracked decision has a status derived at query time — never stored. This makes Bicameral immune to rebase, squash, and cherry-pick.

| Status | Meaning |
|---|---|
| **reflected** ✓ | Code was verified to implement this decision |
| **drifted** ⚠ | Code changed since the decision was last verified |
| **ungrounded** ○ | Decision tracked, but no matching code region found |
| **pending** | Code region found, but not yet verified by the agent |
| **gap** ◈ | Open question — a known unknown that needs an answer before the code can be correct |
| **superseded** — | Replaced by a later decision |

### When Does `link_commit` Run?

`link_commit` syncs a commit's changes into the ledger — it recomputes content hashes and re-evaluates drift for every bound decision.

It fires automatically in three ways:

1. **After every `bicameral.ingest`** — auto-chained by the server
2. **After git commits/merges/pulls** — via the PostToolUse hook installed by `setup`
3. **Before every `bicameral.preflight`** — lazy catch-up if HEAD has advanced since the last sync

If you commit outside of Claude Code (e.g., from a terminal), the next preflight call will sync the ledger before surfacing context.

### Collaboration Modes

| | Solo (default) | Team |
|---|---|---|
| **Who** | Individual eval or single-dev use | Any mix of devs, PMs, designers |
| **Storage** | Local only (gitignored) | Local DB + git-committed event files |
| **Sharing** | Nothing shared | Normal `git push`/`git pull` |
| **Merge conflicts** | N/A | Zero — per-user append-only files |

In **team mode**, a PM ingests a PRD; when a dev pulls, `preflight` surfaces those decisions as coding context and the dashboard shows what still needs implementation.

```
.bicameral/
├── events/                ← committed to git (shared decisions)
│   ├── pm@co.com.jsonl    ← PM's ingested PRDs and transcripts
│   └── dev@co.com.jsonl   ← developer's commit syncs
├── config.yaml            ← committed (mode, guided flag)
└── local/                 ← gitignored (materialized state, DB)
```

---

## What `setup` Installs

Running `bicameral-mcp setup` writes these files to your repo:

| File | What it is | Required? |
|---|---|---|
| `.mcp.json` | MCP server config for Claude Code | Yes — registers the server |
| `.bicameral/config.yaml` | Collaboration mode (`solo`/`team`) and guided-mode flag | Yes — stores your preferences |
| `.bicameral/ledger.db` | SurrealDB decision ledger (solo mode) | Auto-created on first tool call |
| `.gitignore` entry | Ignores `.bicameral/` in solo mode | Recommended |
| `.claude/settings.json` | PostToolUse hook: auto-calls `bicameral.link_commit` after git commits | Optional — improves sync |
| `.claude/settings.json` | SessionEnd hook: runs `bicameral-capture-corrections` to catch uningested mid-session decisions | Optional — closes correction capture gap |
| `.claude/skills/bicameral-*/SKILL.md` | Slash commands (`/bicameral:ingest`, `/bicameral:preflight`, `/bicameral:capture-corrections`, etc.) | Recommended |

### Removing Bicameral

To fully uninstall from a repo:

```bash
# 1. Remove the MCP server
claude mcp remove bicameral --scope project

# 2. Remove data and config
rm -rf .bicameral/

# 3. Remove skills
rm -rf .claude/skills/bicameral-*/

# 4. Remove the git hook (if installed)
#    Open .claude/settings.json and delete the PostToolUse entry
#    with "bicameral" in the command field.

# 5. Remove the .gitignore entry
#    Delete the "# Bicameral MCP" block from .gitignore.
```

---

## Slash Commands

After setup, Claude Code gets these slash commands:

| Command | When to use |
|---|---|
| `/bicameral:ingest` | Paste a transcript, PRD, or Slack thread to track its decisions |
| `/bicameral:preflight` | Surface relevant decisions and drift before implementing |
| `/bicameral:history` | See all tracked decisions grouped by feature area |
| `/bicameral:dashboard` | Open the live decision dashboard in your browser |
| `/bicameral:reset` | Wipe and replay the ledger (emergency use) |

The agent also fires these automatically — `preflight` before any code change, `ingest` when you paste a document.

---

## MCP Tools Reference

<details>
<summary><strong>13 tools across three categories</strong></summary>

### Decision Ledger

| Tool | Purpose |
|---|---|
| `bicameral.ingest` | Ingest a transcript, PRD, or Slack export into the ledger |
| `bicameral.preflight` | Pre-flight: surface prior decisions and drift before coding |
| `bicameral.search` | Search past decisions by topic |
| `bicameral.brief` | Full brief for a feature area (decisions, drift, divergences, gaps) |
| `bicameral.history` | Read-only snapshot of all decisions grouped by feature |
| `bicameral.link_commit` | Sync a commit — update content hashes, re-evaluate drift |
| `bicameral.drift` | Detect drift for decisions touching a specific file |
| `bicameral.judge_gaps` | Run the business-requirement gap rubric on a topic |
| `bicameral.resolve_compliance` | Write back caller-LLM compliance verdicts (compliant/drifted/not_relevant) |
| `bicameral.ratify` | Record product sign-off on a decision |
| `bicameral.update` | Check for and apply recommended version updates |
| `bicameral.reset` | Wipe the ledger for the current repo (dry-run by default) |
| `bicameral.dashboard` | Start the local dashboard server and return its URL |

### Code Locator

The server exposes deterministic primitives only. Callers use their own
tools (Grep/Read/Glob) for code search and pass resolved file paths /
symbol names back to the server.

| Tool | Purpose |
|---|---|
| `validate_symbols` | Fuzzy-match symbol name hypotheses against the code index |
| `get_neighbors` | 1-hop structural graph traversal (callers, callees, imports) |
| `extract_symbols` | Tree-sitter symbol extraction from a source file |

</details>

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `REPO_PATH` | `.` | Path to the repository being analyzed |
| `SURREAL_URL` | `surrealkv://~/.bicameral/ledger.db` | SurrealDB URL. Use `memory://` for tests. |
| `CODE_LOCATOR_SQLITE_DB` | *(auto)* | Override path for the code index database |
| `BICAMERAL_AUTHORITATIVE_REF` | *(auto-detected)* | Override the main branch name (default: reads `origin/HEAD`) |
| `BICAMERAL_PREFLIGHT_MUTE` | `0` | Set to `1` to silence preflight for one session |
| `BICAMERAL_GUIDED_MODE` | *(from config.yaml)* | Set to `1` to force guided (blocking) mode |

All data stays local. The embedded SurrealDB instance runs in-process — no separate server.

---

## Local Development

```bash
git clone https://github.com/BicameralAI/bicameral-mcp.git
cd bicameral-mcp
pip install -e "pilot/mcp[test]"
cd pilot/mcp && pytest tests/ -v
```

Tests use real adapters with `SURREAL_URL=memory://` — no external services required. CI runs on PRs to `main` via GitHub Actions.

---

## Telemetry

Bicameral collects anonymous usage statistics to improve reliability and prioritize development. No code, decision content, file paths, or personally identifiable information is ever collected.

**What is collected:**
- Tool name (e.g. `bicameral.ingest`)
- Server version
- Call duration (milliseconds)
- Error flag (boolean)
- Aggregate counts (e.g. number of decisions grounded per ingest call — integers only)

**What is never collected:** decision descriptions, transcript content, search queries, file paths, repo names, or any user-supplied text.

**Opt out at any time:**

```bash
export BICAMERAL_TELEMETRY=0
```

or add `BICAMERAL_TELEMETRY=0` to the `env` block in your `.mcp.json`.

### Collaborator access

Telemetry data is stored in a private PostHog project. If you are a design partner, contributor, or researcher who needs access to the usage dashboard, reach out directly at **jin@bicameral-ai.com**.

---

## Contributing

Contributions welcome. Please open an issue before submitting large changes.

---

## License

MIT
