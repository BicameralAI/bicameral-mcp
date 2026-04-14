---
name: bicameral-reset
description: Emergency trust recovery for a polluted or stale ledger. Fires when the user says "my ledger looks wrong", "nuke the ledger", "start over", "this is polluted", or otherwise loses trust in the current state. DRY RUN BY DEFAULT — always confirms with the user before the destructive call.
---

# Bicameral Reset

The fail-safe valve. When the user sees a clearly wrong anchor, a polluted drift report, or a ledger that doesn't match reality, they need a one-command path to recover trust. `bicameral.reset` wipes every row scoped to the current repo and returns a replay plan listing the `source_cursor` rows that existed before the wipe, so the user (or Claude) can re-run the original `bicameral_ingest` calls from scratch.

## When to fire

- User says *"my ledger looks polluted"*, *"this is wrong, start over"*, *"nuke the ledger"*, *"wipe it and retry"*
- User complains about a clearly-wrong anchor and asks how to fix it
- After a failed bulk ingest or a transcript that produced garbage groundings
- When re-ingesting produces worse results than the first ingest (sign of cache poisoning)

## When NOT to fire

- **Never fire automatically.** The user must explicitly ask for a reset.
- Individual bad decisions should be removed via `bicameral_forget` (coming in a later release), NOT a full wipe.
- If only one ingest looks bad, suggest re-running THAT ingest against the current ledger rather than wiping everything.
- Drift reports that look wrong are usually a symptom of stale baselines — try `bicameral.link_commit` first and only escalate to `bicameral.reset` if that doesn't help.

## The two-call pattern

`bicameral.reset` is ALWAYS called twice:

1. **Dry run (no flags needed):** `bicameral.reset()` — returns the wipe plan (`cursors_before`, `replay_plan`, `next_action`) without touching any data.
2. **Confirm:** `bicameral.reset(confirm=True)` — only AFTER the user has seen the dry-run summary and explicitly said yes.

Never skip the dry run. Never call `confirm=True` without reading the dry-run response aloud to the user and waiting for confirmation.

## How to present the dry-run response

Show the user, verbatim:
- `cursors_before` — how many source_cursor rows would be wiped
- `replay_plan` — the source_type / source_scope / last_source_ref triples
- `next_action` — the suggested next command

Then ask: *"Confirm by replying 'yes, reset' and I'll run it with `confirm=True`."* Wait for the literal confirmation.

## How to present the wipe response

After a successful `confirm=True` call:
- Confirm the wipe happened: `wiped: true`, `cursors_before: N`
- Show the replay plan again so the user knows what to re-ingest
- Walk the user through re-running each `bicameral_ingest` call, one source at a time

If `wiped=false` and there's a `replay_errors` entry, the wipe failed before persisting. No data was destroyed. Surface the error and stop.

## Example

**User:** *"My ledger is showing this decision grounded to the wrong file — like totally wrong. I don't trust anything in here. Can we nuke it and start over?"*

**Claude:** *"That's what `bicameral.reset` is for. Let me check what we'd be wiping first."*

→ Calls `bicameral.reset()` (dry run)

**Response:**
```
wiped: false
cursors_before: 12
replay_plan: [
  {source_type: slack, source_scope: accountable-tech, last_source_ref: 1776113809.330439},
  {source_type: slack, source_scope: accountable-strategy, last_source_ref: 1776107915.567609},
  {source_type: notion, source_scope: default, last_source_ref: prd-calendar-v2},
  ... (9 more)
]
next_action: Dry run only. Would wipe 12 source_cursor row(s) ...
```

**Claude to user:** *"I'd be wiping 12 recorded sources: 2 Slack channels, 1 Notion page, and 9 others from the last week. After the wipe, I can walk you through re-ingesting each one to rebuild a clean ledger. Confirm by replying 'yes, reset' and I'll run it."*

**User:** *"Yes, reset."*

→ Calls `bicameral.reset(confirm=True)`

**Response:**
```
wiped: true
cursors_before: 12
replay_plan: [... same as before ...]
next_action: Ledger wiped for repo ... Re-run the original bicameral_ingest calls for each entry in replay_plan to repopulate.
```

**Claude to user:** *"Wiped. Here's the replay plan — we have 12 sources to re-ingest. Want to start with the most recent Slack channel (accountable-tech), or do you have a different priority?"*

## Safety rules

1. **NEVER call `confirm=True` without an explicit user yes.** Even if the user said "nuke it" earlier in the conversation, the dry-run and confirmation flow still runs.
2. **Never auto-fire.** Reset is always user-initiated.
3. **Scoped by repo.** The wipe only touches rows for the current repo. Other repos sharing the same SurrealDB instance are unaffected — reassure the user of this if they're working on multiple projects.
4. **Replay is a handoff.** Bicameral does NOT store raw source documents. "Replay plan" means the caller still needs the original transcripts to re-ingest them.
