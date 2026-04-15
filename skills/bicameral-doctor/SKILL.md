---
name: bicameral-doctor
description: Auto-detecting drift health check. Default entry point for "what's drifted" / "what's broken" / "run a health check" / "is anything wrong" — picks file-scope or branch-scope automatically based on whether the user named a specific file. Replaces the v0.4.17 bicameral.drift + bicameral.scan_branch split as the single user-facing "check for drift" tool.
---

# Bicameral Doctor

The default answer when the user asks *"is anything broken?"* / *"what's drifted?"* / *"run a health check."* Doctor picks the right scope automatically — file, branch, or empty — and the agent doesn't need to know which sub-tool ran.

## When to fire

Fire on any of these phrasings (not exhaustive — match the intent):

- *"what's drifted"* / *"what's broken"* / *"what's wrong"* / *"check for drift"*
- *"run a health check"* / *"doctor my branch"* / *"diagnose this"*
- *"scan my PR"* / *"review this branch"* / *"before I merge"*
- *"is `<file>` drifted"* / *"check `<file>`"* (file name pinned → file scope)
- Any discrepancy check the user asks for without specifying which sub-tool

**Doctor is the default.** If you're choosing between `bicameral.scan_branch`, the old `bicameral.drift`, `bicameral.status`, or `bicameral.search` for a user's "check for drift" request, prefer `bicameral.doctor`. It composes the right combination under the hood.

## When NOT to fire

- **Topic-scoped questions without file or drift intent** — *"what was decided about rate limiting?"* — use `bicameral.search`.
- **Pre-implementation context** — *"I'm about to add a Stripe webhook"* — use `bicameral.preflight`.
- **Meeting prep** — *"brief me on calendar sync"* — use `bicameral.brief`.
- **Explicit ledger wipe** — *"nuke the ledger"* — use `bicameral.reset`.

## Tool call

```
bicameral.doctor(
  file_path="<repo-relative path>",   # optional — triggers file scope
  base_ref="<git ref>",               # optional — branch base, defaults to authoritative ref
  head_ref="HEAD",                    # optional — branch head
  use_working_tree=false,             # optional — include uncommitted changes
)
```

**Auto-detection rules**:

- **`file_path` given** → file scope. Doctor runs a single-file drift check on that path (same semantics the old `bicameral.drift` tool provided). Surface the result under `response.file_scan`.
- **`file_path` absent** → branch scope. Doctor runs a branch-scoped sweep of every decision touching files changed between `base_ref` and `head_ref`, plus a compact repo-wide status summary for context. Surface `response.branch_scan` + `response.ledger_summary`.
- **Nothing to scan** (no file, no branch range, empty ledger) → `response.scope == "empty"`. Say so plainly: *"No tracked decisions in scope yet. Drop a meeting transcript or PRD into `bicameral.ingest` to start building the ledger."*

## Response shape

The handler returns a `DoctorResponse` with:

- `scope` — `"file"` / `"branch"` / `"empty"`. Load-bearing — the skill renders a different section based on this.
- `file_scan` — a full `DetectDriftResponse` when `scope == "file"`, else `None`
- `branch_scan` — a full `ScanBranchResponse` when `scope == "branch"`, else `None`
- `ledger_summary` — `DoctorLedgerSummary` with repo-wide `total`, `drifted`, `pending`, `ungrounded`, `reflected` counts. Populated on branch scope only.
- `action_hints` — merged from whichever sub-scan produced them. Same intensity-gated semantics as every other skill (`guided_mode` controls `blocking`).

## How to render

### Scope = file

Render like the old drift output:

1. Lead with a one-line summary: *"`<file_path>`: N drifted, M pending, K reflected."*
2. Drifted entries first, verbatim source excerpts + drift evidence.
3. Pending next.
4. Reflected last, compact.
5. Undocumented symbols at the bottom.
6. Action hints verbatim (only if populated — file scope rarely produces hints in v0.4.18).

### Scope = branch

1. Lead with a two-line summary:
   *"Scanned `<N>` changed files between `<base_ref>..<head_ref>`. `<drifted_count>` drifted on this branch, `<ungrounded_count>` ungrounded, `<pending_count>` pending, `<reflected_count>` reflected."*
   *"Ledger health: `<total>` total decisions tracked repo-wide (`<ledger_summary.drifted>` drifted, `<ledger_summary.pending>` pending, `<ledger_summary.ungrounded>` ungrounded)."*

2. If `branch_scan.sweep_scope == "range_truncated"`, explicitly note it: *"Scanned the first 200 files; the branch changed more. Split into smaller ranges if you care about the tail."*

3. If `branch_scan.sweep_scope == "head_only"`, explicitly note it: *"The base ref was unreachable (shallow clone / force-pushed / missing). Fell back to HEAD-only scope — results cover the most recent commit only."*

4. **Drifted decisions first** (from `branch_scan.decisions` filtered by `status == "drifted"`). Each with description, `symbol:lines`, `source_ref` + `meeting_date`, `source_excerpt` verbatim, `drift_evidence`.

5. **Ungrounded decisions**, with a one-line hint that they need `bicameral.ingest` with fresh grounding or a manual sign-off.

6. **Pending decisions**, compact.

7. **Reflected**, compact (count only, unless the user asked for detail).

8. **Branch-vs-ledger framing** in the trailing summary: if `ledger_summary.drifted > branch_scan.drifted_count`, mention that there are drifted decisions elsewhere in the repo not touched by this branch — useful context for the reviewer.

9. **Action hints verbatim.**

### Scope = empty

Short and honest:

```
No tracked decisions in scope yet. Drop a meeting transcript or PRD
into bicameral.ingest to start building the decision ledger.
```

Don't pretend the scan ran. Don't invent findings.

## Action hints

Same contract as every other read skill. Hint kinds that can fire on doctor responses:

- **`review_drift`** — at least one decision in the scan (file or branch) is drifted. Refs: drifted intent_ids + file paths (on branch scope) or the single file path (on file scope).
- **`ground_decision`** — at least one decision has no code grounding yet. Refs: ungrounded intent_ids.

In **normal mode** (`guided: false`, default), hints are advisory (`blocking: false`). Mention them in one line and continue. In **guided mode** (`guided: true`), hints are blocking. Pause before any write operation and wait for user acknowledgment.

**Never paraphrase a hint's `message` field.** Surface it verbatim.

## Example output (branch scope)

**User**: *"run a health check before I ship this"*

**Call**: `bicameral.doctor()` (no args — branch scope auto-detected)

**Response** (`scope="branch"`, 14 files changed, 3 drifted, 1 ungrounded, 11 reflected; ledger-wide: 47 total, 5 drifted):

```
Scanned 14 changed files between main..HEAD.
 3 drifted on this branch · 1 ungrounded · 11 reflected.

Ledger health: 47 decisions tracked repo-wide — 5 drifted, 2 pending,
 1 ungrounded. 2 of those 5 repo-wide drifts are outside this branch.

⚠ DRIFTED on this branch (3) — resolve before merge:

  ⚠ Apply 10% discount on orders ≥ $100
    DiscountService.calculate:42-67
    Source: sprint-14-planning · 2026-03-12
    "discounts are 10% on orders of $100 or more. below that, no discount."
    Drift: content hash mismatch — the function body no longer matches
    the stored baseline.

  ⚠ Token bucket rate limit, 100 RPM per IP
    CheckoutLimiter.check_rate:42-78
    Source: sprint-13-arch-review · 2026-03-30
    Drift: content hash mismatch — current code uses 200 RPM.

  ⚠ Retry failed webhooks with exponential backoff (max 5 attempts)
    WebhookQueue.scheduleRetry:18-45
    Source: PR #261 review · 2026-03-22
    Drift: content hash mismatch.

○ UNGROUNDED (1):
  ○ Partial-payment recovery flow
    Source: sprint-planning · 2026-04-15
    Needs fresh grounding — call bicameral.ingest with the symbol hint
    or mark it as a manual sign-off.

✓ REFLECTED (11) — no action needed.

Note — 3 matched decision(s) have drifted. Resolve before merging.
```

In guided mode the trailing note becomes: *"⚠ BLOCKING: 3 matched decision(s) have drifted — review the drifted regions and confirm the code still matches stored intent BEFORE making changes."* and the agent pauses.

## Example output (file scope)

**User**: *"is `src/pricing/discount.py` drifted?"*

**Call**: `bicameral.doctor(file_path="src/pricing/discount.py")`

**Response** (`scope="file"`):

```
src/pricing/discount.py — 1 drifted decision.

⚠ DRIFTED: Apply 10% discount on orders ≥ $100
  DiscountService.calculate:42-67
  Source: sprint-14-planning · 2026-03-12
  "discounts are 10% on orders of $100 or more. below that, no discount."
  Drift: content hash mismatch — the function body no longer matches
  the stored baseline.

2 decisions pending, 4 reflected.
1 undocumented symbol in the file: calculate_shipping.
```

## Example output (empty scope)

**User**: *"check for drift"* (but the ledger is empty)

**Call**: `bicameral.doctor()`

**Response** (`scope="empty"`):

```
No tracked decisions in scope yet. Drop a meeting transcript or PRD
into bicameral.ingest to start building the decision ledger.
```

## Rules

1. **Doctor is the default.** When the user asks about drift / health / what's broken, reach for `bicameral.doctor`, not the sub-tools.
2. **Don't fan out.** Doctor already composes scan_branch + ledger summary in one call. Don't also call scan_branch separately as a "second opinion."
3. **Honor the scope field.** The handler picked file / branch / empty for a reason — render the corresponding section, don't improvise.
4. **Verbatim source excerpts.** Quote `source_excerpt` directly. Don't paraphrase the meeting language.
5. **Hints verbatim.** `action_hints[].message` is pre-formatted. Surface it as-is.
6. **Branch-vs-ledger contrast is useful signal.** If the repo has drifted decisions outside the current branch, say so — reviewers care.

## Migration notes (from pre-v0.4.18)

- `bicameral.drift` was removed in v0.4.18. File-scoped behavior now lives inside `bicameral.doctor(file_path=...)`. Same per-decision output shape, nested inside `response.file_scan`.
- `bicameral.scan_branch` is still callable directly but should only be used when the user explicitly wants the raw branch sweep without the ledger-wide context. Prefer `bicameral.doctor()` for the default case.

## Arguments

$ARGUMENTS — optional file path / base ref / head ref / working-tree flag.
