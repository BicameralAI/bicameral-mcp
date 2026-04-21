---
name: bicameral-scan-branch
description: Multi-file drift audit for a branch. Fires on "what's drifted on this branch", "scan my PR", "is anything broken before I merge", or any whole-branch / multi-file discrepancy check. This is the default answer to "check for drift" — use bicameral.drift only when the user explicitly names a single file.
---

# Bicameral Scan Branch

Audit every decision that touches any file the current branch changed, in a single call. This is the multi-file counterpart to `bicameral.drift` and the default tool for "check drift" requests that don't name a specific file.

## When to fire

Fire `bicameral.scan_branch` on any of these phrasings (not exhaustive — match the intent):

- *"what's drifted on this branch"*
- *"scan my PR"*
- *"check the whole branch for discrepancies"*
- *"is anything broken before I merge"*
- *"review this branch"*
- *"before I ship this, what's drifted"*
- *"what's wrong with the changes I made"*
- *"doctor my branch"* (pre-v0.4.18 shim — `bicameral.doctor` is the v0.4.18 composition layer)
- Any phrasing that implies **multi-file scope** without naming a specific file

Default heuristic: if the user says "drift" / "check" / "scan" / "review" without a file path, assume they mean the whole branch. A single-file scope is the exception, not the default.

## When NOT to fire

- **Single-file questions** the user explicitly names — *"is `src/pricing/discount.py` drifted?"* — use `bicameral.drift` on that one file. Don't fan out scan_branch when the scope is clearly one file.
- **Topic-scoped questions** without file scope — *"what was decided about rate limiting?"* — use `bicameral.search` (topic search over the decision ledger).
- **Ledger-wide status** regardless of branch — *"show me every drifted decision across the whole repo"* — use `bicameral.status filter=drifted`.
- **Read-only explanation** questions — *"how does the rate limiter work?"* — don't fire any drift tool.

## Tool call

```
bicameral.scan_branch(
  base_ref="<git ref to diff from>",    # optional, default: BICAMERAL_AUTHORITATIVE_REF or 'main'
  head_ref="<git ref to diff to>",      # optional, default: HEAD
  use_working_tree=false,               # optional, default: False (PR-review posture)
)
```

Call arguments:

- **`base_ref`** — the ref the branch is being reviewed against. Defaults to the authoritative branch (`main` by convention, or whatever `BICAMERAL_AUTHORITATIVE_REF` is set to). Accepts branch names, tags, or SHAs. If the user says "compared to main" use `"main"`; if they say "compared to the last release" ask them which tag or find it from conversation context.
- **`head_ref`** — the branch tip being audited. Usually `HEAD`. Override only when the user specifically names another ref.
- **`use_working_tree`** — set to `true` for a pre-commit sweep (include uncommitted changes). Leave `false` for PR-review posture (committed changes only, same as what a reviewer would see).

## Response shape

The handler returns a `ScanBranchResponse` with:

- `base_ref` / `head_ref` — the resolved refs that were diffed
- `sweep_scope` — `"range_diff"` (default, good), `"head_only"` (base was unreachable — fell back to HEAD-only scope, surface to user), or `"range_truncated"` (range exceeded the 200-file cap; the scan ran on the first 200 files, rest need a separate pass)
- `range_size` — number of files the sweep covered
- `decisions` — deduped list of `DriftEntry` across all files (each decision shows up once even if it touches multiple files)
- `files_changed` — the file paths that were swept
- `drifted_count` / `pending_count` / `ungrounded_count` / `reflected_count`
- `undocumented_symbols` — union across all files
- `action_hints` — populated when findings exist (see "Action hints" below)

## How to render the response

Present the response in this order:

1. **Lead with a one-line summary.** *"Scanned 14 changed files across `main..HEAD`. 3 drifted, 1 ungrounded, 4 reflected."* Makes the scope visible before the details. Include `sweep_scope` if it's `range_truncated` or `head_only` — the user needs to know the scan was partial.

2. **Drifted decisions first.** Group by status; drifted is always the top bucket. For each drifted entry, surface: description, `symbol:start_line-end_line`, `source_ref` + `meeting_date`, `source_excerpt` (quote verbatim), `drift_evidence`. The user's first agenda item is always resolving these before the PR merges.

3. **Ungrounded decisions next.** Decisions that touch changed files but have no code grounding. Present with description + `source_ref` and a one-line hint that these need `bicameral.ingest` with fresh grounding or a manual sign-off.

4. **Pending decisions after that.** Decisions in scope but still pending implementation. Usually fine to list compactly.

5. **Reflected decisions last, compact.** These are the "no action needed" decisions. Include the count but don't list each one unless the user asked for detail.

6. **Undocumented symbols at the bottom.** Union of symbols across the changed files that have no decision mapping at all. Present as a simple list with a one-liner: *"These symbols changed but aren't tracked by any decision — worth a sanity check."*

7. **Action hints last**, verbatim. See below.

## Action hints

Same contract as `bicameral-search`, `bicameral-brief`, and `bicameral-drift`. The response's `action_hints` list carries zero or more hints with `kind`, `message`, `blocking`, `refs`. Two intensities controlled by the `guided` flag (or `BICAMERAL_GUIDED_MODE` env var).

- **Normal mode** (`guided: false`, default) — hints fire on findings but with `blocking: false` and advisory tone. Mention them to the user in one line and continue. It's a heads-up, not a stop sign.
- **Guided mode** (`guided: true`) — same hints with `blocking: true` and imperative tone. Address each blocking hint before any write operation (file edit, commit, PR, `bicameral_ingest`).

Kinds that can fire on scan_branch responses:

- **`review_drift`** — at least one scanned decision is drifted. Refs: drifted `decision_id`s + the files that were changed in the sweep.
- **`ground_decision`** — at least one scanned decision has no code grounding yet. Refs: ungrounded `decision_id`s.

**Never paraphrase a hint's `message` field** — surface it verbatim. The phrasing intentionally varies between normal and guided mode so the user can tell at a glance whether the agent is being advised or required to pause.

## Example output

**User**: *"scan my branch for drift before I merge this"*

**Call**: `bicameral.scan_branch()` (all defaults)

**Response** (`sweep_scope=range_diff`, `range_size=14`, `drifted_count=3`, `pending_count=1`, `ungrounded_count=0`, `reflected_count=11`):

```
Scanned 14 changed files between main..HEAD.
 3 drifted · 1 pending · 11 reflected.

⚠ DRIFTED (3) — resolve before merge:

  ⚠ Apply 10% discount on orders ≥ $100
    DiscountService.calculate:42-67
    Source: sprint-14-planning · 2026-03-12
    "discounts are 10% on orders of $100 or more. below that, no discount."
    Drift: content hash mismatch — the function body no longer matches
    the stored baseline.

  ⚠ Token bucket rate limit, 100 RPM per IP
    CheckoutLimiter.check_rate:42-78
    Source: sprint-13-arch-review · 2026-03-30
    "cap checkout at 100 RPM per IP, token bucket on the endpoint"
    Drift: content hash mismatch — current code uses 200 RPM.

  ⚠ Retry failed webhooks with exponential backoff (max 5 attempts)
    WebhookQueue.scheduleRetry:18-45
    Source: PR #261 review · 2026-03-22
    Drift: content hash mismatch.

◐ PENDING (1):
  ◐ Partial-payment recovery flow
    Source: sprint-planning · 2026-04-15

✓ REFLECTED (11) — no action needed.

Note — 3 matched decision(s) have drifted. Resolve before merging.
```

(In guided mode, the last line reads: *"⚠ BLOCKING: 3 matched decision(s) have drifted — review the drifted regions and confirm the code still matches stored intent BEFORE making changes."* And the agent pauses.)

## Rules

1. **Multi-file is the default scope.** If the user says "drift" or "check" without a file path, reach for `scan_branch`, not `bicameral.drift`. File-scoped drift is the exception.
2. **Never silently truncate.** If `sweep_scope == "range_truncated"`, surface that to the user — *"Scanned the first 200 files; branch changed more than that. Split into smaller ranges or narrow the scope."*
3. **Drifted first, always.** Even if drifted_count is small vs reflected, drifted is the load-bearing bucket — lead with it.
4. **Verbatim source excerpts.** Quote `source_excerpt` directly. Don't paraphrase the meeting language — the value is in citing what was decided, word for word.
5. **Hints verbatim.** `action_hints[].message` is pre-formatted. Surface it as-is; the mode-dependent tone is the user's signal.
6. **Don't fan out parallel `bicameral.drift` calls** as a workaround for multi-file scope — that's what pre-v0.4.17 agents did, and the whole point of `scan_branch` is to replace that pattern with one call.

## Arguments

$ARGUMENTS — optional base ref / head ref / working-tree flag; otherwise use defaults.
