---
name: bicameral-sync
description: Full ledger sync after a git COMMIT — runs bicameral.link_commit then evaluates pending compliance checks to write reflected/drifted verdicts. ONLY for post-commit ledger sync. DO NOT trigger for "update", "upgrade", or "new version" requests — those belong to /bicameral:update (binary upgrade). Trigger on: PostToolUse hook "bicameral: new commit detected", _sync_guidance in any tool response, or explicit "sync", "check compliance", "reflect this commit".
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

**Never fire for**: "update", "upgrade", "new version", "install update" — those are binary
upgrade requests; use `/bicameral:update` instead.

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

> **Phase 3+4 (#60+#61) — `enhance_drift` mode.** When the
> `BICAMERAL_CODEGENOME_ENHANCE_DRIFT` flag is on, `link_commit` runs
> two pre-passes BEFORE you see this list:
>
> 1. **Continuity matcher** — auto-redirects bindings whose symbol
>    moved or was renamed. Stripped regions appear in
>    `link_commit_response.continuity_resolutions` with
>    `semantic_status` ∈ `{identity_moved, identity_renamed,
>    needs_review}`. `needs_review` (confidence 0.50–0.75) is
>    advisory — the binding was NOT redirected; treat as a normal
>    pending check.
>
> 2. **Cosmetic-vs-semantic classifier** — auto-resolves regions
>    whose change is structurally cosmetic (docstring/comment/import
>    re-order/whitespace, with same signature + neighbors). Stripped
>    regions get a `compliance_check` row written by the server with
>    `verdict="compliant", semantic_status="semantically_preserved"`,
>    `evidence_refs=[…]`. The count is reported as
>    `link_commit_response.auto_resolved_count`.
>
> Pendings that survive both passes may carry a typed
> `pre_classification: PreClassificationHint | None` field when the
> classifier scored the change in the uncertain band [0.30, 0.80).
> The hint includes `verdict` ("uncertain"), `confidence`, per-signal
> contributions, and `evidence_refs`. Use it as advisory evidence
> when reasoning about your verdict — your decision still wins.
>
> With `enhance_drift` off (the default), both passes are no-ops and
> the pre-Phase-3 behaviour is preserved.

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
    decision_id:    "<check.decision_id>",
    region_id:      "<check.region_id>",
    content_hash:   "<check.content_hash — echo exactly>",
    verdict:        "compliant" | "drifted" | "not_relevant",
    confidence:     "high" | "medium" | "low",
    explanation:    "<one sentence: why this code does/doesn't match the decision>",

    # Phase 4 (#61) — optional. Pass when you want to claim the
    # cosmetic-vs-semantic axis explicitly. Both default to None / [].
    semantic_status: "semantically_preserved" | "semantic_change" | None,
    evidence_refs:  ["any:audit-trail-string", ...],
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
