---
name: bicameral-diagnose
description: Read-only structural diagnosis of the local bicameral ledger. Fires on "what's wrong with my ledger", "diagnose the ledger", "is bicameral broken", "ledger health check", or any user-facing tool error that mentions schema/migration/SurrealDB. Calls `bicameral.diagnose` (MCP) which uses a raw client and works even when the normal connect path crashes. Returns a structured `recovery_path` (clean / fixable / reset_rebuild / reset_destructive) the agent surfaces alongside the recommended next command. Never mutates state — repair is a CLI operation (`bicameral-mcp diagnose --repair`) or a deliberate reset call.
---

# Bicameral Diagnose

Read-only diagnostic for the local ledger. The MCP tool surface for the same `bicameral-mcp diagnose` CLI you'd paste into a bug report — but agent-callable from a tool-error envelope, and resilient to the failure modes that crash normal `connect()`.

## When to fire

- User asks "what's wrong with my ledger" / "is bicameral broken" / "ledger health check" / "diagnose the ledger".
- Any tool error envelope from another bicameral tool that mentions `LedgerError`, `SchemaVersionTooNew`, `DestructiveMigrationRequired`, or a SurrealDB error string the user can't action.
- Before recommending `bicameral_reset` — diagnose first, choose the recovery path on the basis of the response, then propose the matching reset call.

## When NOT to fire

- For questions about decisions or drift — that's `bicameral.history` / `bicameral.preflight`. Diagnose is about the ledger storage layer.
- To repair the ledger. Diagnose is read-only by contract. The repair surface is `bicameral-mcp diagnose --repair` (CLI, user-driven) or a deliberate `bicameral_reset` call. Surface the diagnosis, then surface the recommended next command — never silently retry.

## Output contract

The MCP tool returns:

```jsonc
{
  "ledger_url": "surrealkv:///Users/.../ledger.db",
  "connect_error": "",        // non-empty when raw connect itself failed
  "recovery_path": "clean" | "fixable" | "reset_rebuild" | "reset_destructive",
  "diagnosis": {              // null when connect_error is set
    "bicameral_version": "0.14.x",
    "python_version": "3.13.0",
    "platform_str": "...",
    "surrealdb_running": "1.0.4",
    "schema_version_recorded": 16,
    "schema_version_expected": 17,
    "drift_status": "match" | "drift" | "first-write" | "unavailable",
    "table_counts": { "decision": 42, "yields": 100, ... },
    "recent_events": [...],   // last 5 warn|error audit log entries
    "suggestions": [...],     // hardcoded heuristics from the CLI gather
    ...
  },
  "next_action": "human-readable instruction tied to recovery_path"
}
```

## Recovery-path matrix

Render `next_action` verbatim to the user. Then offer the matching command:

| `recovery_path` | What it means | Recommend |
|---|---|---|
| `clean` | Schema matches, tables look sane. | "No remediation needed. If you're still seeing errors, share the exact tool name and arguments." |
| `fixable` | Schema is behind binary; pending migrations will run on next normal connect. | "Run any bicameral tool — it will trigger the migration. If that fails, try `bicameral-mcp diagnose --repair` (CLI)." |
| `reset_rebuild` | Ledger is unrecoverable, but `.bicameral/events/` has events on disk. | "`bicameral_reset(wipe_mode='ledger', replay_from_events=True, confirm=True)` will wipe and rebuild from the event log." |
| `reset_destructive` | Ledger is unrecoverable AND no events on disk → reset loses decision history. | "`bicameral_reset(wipe_mode='ledger', confirm=True)` will wipe; you'll need to re-ingest sources from `replay_plan`." |

For `connect_error`-set responses (raw client itself can't connect), surface the error text and suggest checking the ledger path / file permissions before any reset.

## Two diagnose surfaces — when to use which

| Surface | Role | When |
|---|---|---|
| `bicameral.diagnose` (MCP, this skill) | Agent-callable, read-only, returns structured `recovery_path`. | In-session diagnosis, automatic surfacing from tool errors. |
| `bicameral-mcp diagnose` (CLI) | Human-pasteable markdown for bug reports. Adds `--repair` flag (user-driven repair attempts). | Bug reports, manual repair, terminal sessions without an active agent. |

Both call the same `gather_diagnosis_raw` function — the data is identical. The MCP version emits structured JSON; the CLI emits markdown. Don't render the CLI markdown via this skill; surface the structured fields directly.

## Auto-fire from another tool's error envelope

When another bicameral tool returns an error containing schema/migration vocabulary, the agent should:

1. Call `bicameral.diagnose` immediately (no user prompt — it's read-only and bounded).
2. Render the `recovery_path` and `next_action`.
3. Wait for user confirmation before invoking any reset command. Reset is destructive even when "non-destructive on paper."

Never run `bicameral_reset` on the basis of a diagnose response without explicit user confirmation.
