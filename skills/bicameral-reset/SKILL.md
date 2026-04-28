---
name: bicameral-reset
description: Emergency trust recovery for a polluted or stale ledger. Fires when the user says "my ledger looks wrong", "nuke the ledger", "start over", "this is polluted", or otherwise loses trust in the current state. DRY RUN BY DEFAULT — always confirms with the user before the destructive call. Two modes: ledger (default, wipes DB rows only) and full (deletes entire .bicameral/ directory).
---

# Bicameral Reset

The fail-safe valve. When the ledger gets polluted — bad ingest, stale groundings, or a session that went off the rails — `bicameral.reset` gives you a one-command recovery path.

## Two modes

| Mode | What's deleted | When to use |
|------|----------------|-------------|
| `wipe_mode="ledger"` (default) | Materialized SurrealDB rows only. Config, event files, and team history are preserved. Server stays live. | Bug recovery, bad ingest, polluted groundings. The safe default. |
| `wipe_mode="full"` | The entire `.bicameral/` directory — ledger, `config.yaml`, team event JSONL files, everything. | Nuclear restart: switching repos, credential rotation, complete distrust of all prior decisions. |

## When to fire

- User says *"my ledger looks polluted"*, *"this is wrong, start over"*, *"nuke the ledger"*, *"wipe it and retry"*
- After a failed bulk ingest or a transcript that produced garbage groundings
- When re-ingesting produces worse results than the first ingest (sign of cache poisoning)

**Ask the user which mode they want** before running the dry run. If they say "start over completely" or "wipe everything including config" → `full`. Otherwise default to `ledger`.

## When NOT to fire

- **Never fire automatically.** Reset is always user-initiated.
- Drift reports that look wrong → run `/bicameral:sync` first, escalate to reset only if that doesn't help.
- If only one ingest looks bad, suggest re-running that ingest rather than wiping everything.

## The two-call pattern (always)

1. **Dry run:** `bicameral.reset(wipe_mode=<mode>)` — returns the plan without touching state.
2. **Confirm:** `bicameral.reset(wipe_mode=<mode>, confirm=True)` — only after explicit user yes.

Never skip the dry run. Never pass `confirm=True` without reading the dry-run summary to the user.

## How to present the dry-run response

Show the user:
- `wipe_mode` — which mode will be used
- `bicameral_dir` — the directory that will be deleted (full mode only, **always show this**)
- `cursors_before` — how many source_cursor rows would be wiped
- `replay_plan` — the source_type / source_scope / last_source_ref triples

For **full mode**, read the warning verbatim to the user:

> "This will delete the entire `.bicameral/` directory at `<bicameral_dir>`, including your config.yaml and all team event history. There is no undo. Confirm by replying 'yes, full reset'."

For **ledger mode**, the standard confirmation is sufficient:

> "Confirm by replying 'yes, reset' and I'll run it."

Wait for explicit confirmation before calling with `confirm=True`.

## How to present the wipe response

After a successful `confirm=True` call:
- Confirm: `wiped: true`, `wipe_mode`, `cursors_before: N`
- For full mode, confirm the directory was deleted: `bicameral_dir`
- Show the replay plan and walk the user through re-ingesting each source

If `wiped=false` with a `replay_errors` entry, the wipe failed before persisting — no data was destroyed. Surface the error and stop.

## Example — ledger mode (default)

**User:** *"My ledger is showing totally wrong groundings. Can we wipe it and start over?"*

**Claude:** *"That's `bicameral.reset`. Let me check what we'd be wiping first."*

→ `bicameral.reset()` (dry run, wipe_mode="ledger")

**Claude to user:** *"I'd be wiping 12 recorded sources from the DB. Config and event history are preserved. Confirm by replying 'yes, reset'."*

→ `bicameral.reset(confirm=True)` on user yes.

## Example — full mode

**User:** *"Something is really wrong. Nuke everything — config, events, all of it."*

**Claude:** *"That's `wipe_mode='full'`. Let me show you exactly what would be deleted."*

→ `bicameral.reset(wipe_mode="full")` (dry run)

**Claude to user:** *"This will delete the entire `.bicameral/` directory at `/Users/you/repo/.bicameral`, including your config.yaml and all team event history. There is no undo. Confirm by replying 'yes, full reset'."*

→ `bicameral.reset(wipe_mode="full", confirm=True)` on explicit user yes.

## Safety rules

1. **NEVER call `confirm=True` without an explicit user yes.** Even if they said "nuke it" earlier.
2. **Never auto-fire.** Reset is always user-initiated.
3. **Full mode: always show `bicameral_dir` from the dry-run response** before asking for confirmation.
4. **Replay is a handoff.** Bicameral does not store raw source documents — the replay plan gives you the source refs, but you need the original transcripts to re-ingest.

## Telemetry

**At skill start**:
```
bicameral.skill_begin(skill_name="bicameral-reset", session_id=<uuid4>,
  rationale="<one-liner: e.g. 'user said ledger looks wrong start over'>")
```

**At skill end** (after confirm or after user cancels at dry-run):
```
bicameral.skill_end(skill_name="bicameral-reset", session_id=<stored_id>,
  errored=<bool>, error_class="user_abort" if user cancelled else None)
```
