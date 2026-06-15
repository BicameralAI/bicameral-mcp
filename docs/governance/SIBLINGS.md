# Sibling Registry

**Status**: Active · **Last reviewed**: 2026-06-08

This file is the single source of truth for **sibling tools** — local process,
governance, or AI tooling that touches this checkout but must **never** leak into a
commit or a tracked reference.

The **only** mandatory layer for a pull request is the shared **bic-logic** contract
(the factory-owned process). Below that line, your choice of local tooling is free — as
long as the tool is **registered as a sibling** here and its artifacts stay out of the
repo.

> The maintainer's own process tooling is itself a registered sibling, not a requirement
> placed on contributors. Bring whatever helps you work.

## What a sibling is

A sibling is any tool that:

- runs **locally** against this checkout (a governance system, an agent harness, an IDE
  assistant, a homegrown framework, a sibling product);
- writes **scratch/state** into the working tree (a dotdir, generated docs, logs, plans);
- must be **leak-guarded** — its roots are gitignored and never committed;
- is **never referenced** by any tracked file.

A sibling has **no product authority**. The MCP tools emit ingest, preflight, binding, and
review signals through protocol-compatible paths; they never own canonical state.

## The rules (invariants)

1. **Local-only** — a sibling's artifacts are never committed.
2. **Leak-guarded** — every registered root is in `.gitignore`.
3. **Never-referenced** — no tracked file names a sibling's internal files, paths, or APIs.
4. **Registry ⇔ `.gitignore` agree** — every root below has a matching `.gitignore` entry,
   and every agent-scratch `.gitignore` entry has a row here.

## Registry

| Sibling | Root(s) | Owner | Rule |
|---|---|---|---|
| **Qor-logic** | `.qor/` | maintainer | local process governance; never tracked |
| **FailSafe** | `.failsafe/` | sibling MythologIQ product | leak-prevention only; never referenced |
| **Bicameral factory (local)** | `.bicameral/` | factory tooling | local install state; never tracked |
| **Claude Code** | `.claude/worktrees/` | contributor (AI assistant) | agent scratch; never tracked |
| **Cursor** | `.cursor/` | contributor (AI assistant) | agent scratch; never tracked |
| **Windsurf** | `.windsurf/` | contributor (AI assistant) | agent scratch; never tracked |
| **Generic agent scratch** | `.agent/`, `plan-*.md` | contributor | run plans, context bundles, raw logs; never tracked |

## How to register your own tool

You do **not** need maintainer permission to use your own tooling. To register it:

1. **Add a row** to the table above: tool name, scratch root(s), you as owner, and the rule.
2. **Add the root to `.gitignore`**, so the tool is provably un-committable.
3. **Keep artifacts out of tracked files** — never commit the tool's output and never name
   its internals in a tracked doc.

Open the change as a normal PR; the only thing it must satisfy is the shared bic-logic
contract and a clean working tree. See [`CONTRIBUTING.md`](../../CONTRIBUTING.md) →
*Bring your own tools*.
