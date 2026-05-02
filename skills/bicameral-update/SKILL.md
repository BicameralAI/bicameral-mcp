---
name: bicameral-update
description: Check for and apply a new bicameral-mcp binary release. Upgrades the pip package, reinstalls skills and Claude hooks. NOTHING to do with git commits or ledger sync — those are handled by /bicameral:sync. Trigger on any user request containing "update", "upgrade", "new version", "latest version", or "install update".
---

# Bicameral Update

Check for a new `bicameral-mcp` release and apply it.

**This skill is about upgrading the installed binary.** It has nothing to do
with git commits, ledger sync, or compliance checks — those are `/bicameral:sync`.

## Telemetry

**At skill start**:
```
bicameral.skill_begin(skill_name="bicameral-update", session_id=<uuid4>,
  rationale="<one-liner: e.g. 'user asked to update to latest version'>")
```

**At skill end**:
```
bicameral.skill_end(skill_name="bicameral-update", session_id=<stored_id>,
  errored=<bool>, error_class="<if errored>")
```

## Step 1 — Check for a new version

```
bicameral.update(action="check", current_version=<SERVER_VERSION>)
```

- `status: "up_to_date"` → tell the user they are on the latest version. Done.
- `status: "update_available"` → proceed to Step 2.
- `status: "unknown"` → could not reach version endpoint; tell the user and stop.

## Step 2 — Confirm with the user

Tell the user:

> `bicameral-mcp v{recommended_version}` is available (you are on `v{current_version}`).
> Upgrade now?

Wait for explicit confirmation ("yes" / "no") before proceeding.

## Step 3 — Apply the update

```
bicameral.update(action="apply", current_version=<SERVER_VERSION>)
```

The server will:
1. `pip install bicameral-mcp=={recommended_version}`
2. Reinstall skills into `.claude/skills/`
3. Reinstall Claude hooks in `.claude/settings.json`
4. Install git post-commit hook (Guided mode only)
5. Auto-apply any pending schema migration

**If `migration_applied: true` in the response**, tell the user:
> Schema migration ran automatically. Your ledger data was reset.
> Re-ingest the following to restore it:

Then list `migration_replay_plan` entries and ask: "Re-ingest now? (yes/no)"

## Step 4 — Confirm success

Report: `bicameral-mcp updated to v{recommended_version}. {skills_updated} skill(s) reinstalled.`

Remind the user to **restart their Claude Code session** so the new server binary takes effect.
