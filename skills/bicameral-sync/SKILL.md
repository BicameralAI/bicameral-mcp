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

## Telemetry

**At skill start**:
```
bicameral.skill_begin(skill_name="bicameral-sync", session_id=<uuid4>,
  rationale="<one-liner: e.g. 'user committed and asked to sync decisions'>")
```

**At skill end**:
```
bicameral.skill_end(skill_name="bicameral-sync", session_id=<stored_id>,
  errored=<bool>, error_class="<if errored>")
```

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

### 2.bis Uncertain-band sub-protocol (Phase 4 / #44)

When a `PendingComplianceCheck` carries a `pre_classification` field with
`verdict == "uncertain"`, the deterministic classifier scored the change in
the [0.30, 0.80) band — too cosmetic to auto-resolve, too structural to
short-circuit as semantic. **You are the judge.** Apply this two-axis rubric
on top of the standard verdict flow above:

**Axis 1 — compliance (decided FIRST).** Is this region semantically about
the decision at all?

- *No* — emit `verdict: "not_relevant"` and **leave `semantic_status` unset
  (`None`)**. Axis 2 doesn't apply to misretrieved regions; the server
  will prune the `binds_to` edge. Stop. Do not reason about cosmetic-vs-semantic.
- *Yes* — continue to Axis 2.

**Axis 2 — cosmetic vs semantic (decided SECOND).** Use
`pre_classification.signals` as **advisory** evidence:

| Signal | High value (>0.8) means |
|---|---|
| `signature` | Function shape unchanged → leans cosmetic |
| `neighbors` | Surrounding context unchanged → leans cosmetic |
| `diff_lines` | Only comment / docstring / whitespace lines changed → leans cosmetic |
| `no_new_calls` | No new callees introduced → leans cosmetic |

Read the actual diff. Don't trust the signals blindly — they're advisory,
not authoritative. Then:

- If the change is structurally cosmetic AND the decision's intent is
  unaffected → `semantic_status: "semantically_preserved"`,
  `verdict: "compliant"`.
- If the change is genuinely semantic (logic, threshold, branch, return
  shape changed) → `semantic_status: "semantic_change"`. The verdict
  follows from Axis 1: `compliant` if the new logic still meets the
  decision; `drifted` otherwise.

**Echo the hint's `evidence_refs` back in the verdict's `evidence_refs`** so
the audit trail captures the deterministic→LLM hand-off:

```
bicameral.resolve_compliance(
  phase="drift",
  flow_id="...",
  verdicts=[{
    decision_id:    "...",
    region_id:      "...",
    content_hash:   "...",
    verdict:        "compliant" | "drifted" | "not_relevant",
    confidence:     "high" | "medium" | "low",
    explanation:    "<one sentence covering BOTH axes>",
    semantic_status: "semantically_preserved" | "semantic_change" | None,
    evidence_refs:  ["<echo from pre_classification.evidence_refs>", ...],
  }, ...]
)
```

The two-axis judgment maps to existing typed fields — no new contract.
`PreClassificationHint` (the `pre_classification` you read) and
`ComplianceVerdict` (what you emit) are defined in `contracts.py`.

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
