---
name: bicameral-sync
description: Full ledger sync after a git commit — runs bicameral.link_commit then semantically evaluates every pending compliance check to write verdicts. This is the only path from hash-level drift detection to authoritative reflected/drifted status. AUTO-TRIGGER when the PostToolUse hook prints "bicameral: new commit detected" OR when _sync_guidance appears in any tool response. Also fire explicitly after any git commit, merge, pull, or rebase --continue.
---

# Bicameral Sync

Ensure the decision ledger is fully current after a commit — hash-level AND semantic.

The git post-commit hook (Guided mode) runs `bicameral-mcp link_commit HEAD` automatically.
That gives you hash-level change detection but leaves compliance checks unresolved — status
stays `pending` rather than `reflected` or `drifted`. This skill completes the loop: the LLM
reads each changed region, evaluates it against the stored decision, and writes a verdict.
**Without this skill, status never becomes authoritative.**

## When to fire

- **PostToolUse hook output** contains: `"bicameral: new commit detected"`
- **`_sync_guidance` field** in any bicameral tool response (injected by `ensure_ledger_synced`)
- After any `git commit`, `git merge`, `git pull`, or `git rebase --continue`
- Explicitly: *"sync the ledger"*, *"check compliance after that commit"*, *"what's the status now?"*

## Steps

### 1. Sync HEAD

Call `bicameral.link_commit` to compute hash-level drift for the new commit:

```
bicameral.link_commit(commit_hash="HEAD")
```

**Skip this call** if `_pending_compliance_checks` is already in scope from an
auto-sync injection (the auto-sync already ran `link_commit` for you — use those
checks directly).

### 2. Resolve every pending compliance check

If `pending_compliance_checks` is non-empty (from the `link_commit` response or
from `_pending_compliance_checks` in an auto-sync injection):

For each entry in the list:

1. **Read the code.** `code_body` is pre-extracted (capped at ~200 lines).
   If it looks truncated, read `file_path` directly for full context.

2. **Compare** `decision_description` against `code_body`. Ask: does this code
   *functionally implement* the decision, or just share keywords?
   - `"compliant"` — code implements the decision correctly
   - `"drifted"` — code has diverged (threshold changed, behavior removed, etc.)
   - `"not_relevant"` — retrieval mismatch; this region is unrelated to the decision
     (server will prune the `binds_to` edge)

3. **Batch all verdicts into one call** — never one call per check:

```
bicameral.resolve_compliance(
  phase="drift",
  flow_id="<from link_commit response or _pending_flow_id>",
  verdicts=[{
    decision_id: "<check.decision_id>",
    region_id:   "<check.region_id>",
    content_hash: "<check.content_hash — echo exactly>",
    verdict:     "compliant" | "drifted" | "not_relevant",
    confidence:  "high" | "medium" | "low",
    explanation: "<one sentence: why this code does/doesn't match the decision>"
  }, ...]
)
```

The `content_hash` is a compare-and-set guard — echo it exactly from the check.
If the file changed between the sync and your read, the server rejects the verdict
and the region stays `pending` until the next sweep.

**Skip step 2** when `pending_compliance_checks` is empty — nothing changed or
all regions already had cached verdicts.

### 3. Report

Summarize in one line after `resolve_compliance` completes:

```
Synced <short_hash>: N reflected · N drifted · N pending
```

If any decisions are drifted, name them explicitly — the user needs to see drift
immediately, not on the next preflight. Do not enumerate reflected decisions.

## Rules

1. **Always complete step 2 before responding to the user about anything else.**
   This skill runs autonomously after a commit. Do not wait for user input.
2. **Batch verdicts.** One `resolve_compliance` call for all checks.
3. **Echo `content_hash` exactly.** It's a CAS guard; any mutation rejects the verdict.
4. **`not_relevant` is a pruning signal**, not a failure. Use it freely when the
   server retrieval grabbed a region that doesn't relate to the decision.
5. **Do not re-call `bicameral.link_commit`** if `_pending_compliance_checks` is
   already in scope — the auto-sync already did it.
