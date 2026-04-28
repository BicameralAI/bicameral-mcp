---
name: bicameral-drift
description: Check a single file for drifted decisions before committing or during code review. Surfaces all decisions that touch symbols in the file and flags divergence. Use bicameral-scan-branch for multi-file or whole-branch scope.
---

# Bicameral Drift

Code review check — surface decisions that touch a file and flag any that have drifted from intent.

## When to use

- Before committing changes to a specific file
- During code review / PR review when the user names a single file
- When the user asks "are there any drifted decisions for this file?"

Use `bicameral-scan-branch` instead for multi-file scope or when the user says
"check my branch", "scan my PR", etc.

## Telemetry

**At skill start**:
```
bicameral.skill_begin(skill_name="bicameral-drift", session_id=<uuid4>,
  rationale="<one-liner: e.g. 'user asked is src/auth.py drifted'>")
```

**At skill end**:
```
bicameral.skill_end(skill_name="bicameral-drift", session_id=<stored_id>,
  errored=<bool>, error_class="<if errored — e.g. drift_mislabeled>")
```

## Steps

1. Determine the file path — from $ARGUMENTS, the currently open file, or ask the user.
2. Call `bicameral.drift`:
   ```
   bicameral.drift(
     file_path="<relative path from repo root>",
     use_working_tree=true    # pre-commit: compare against disk
                              # false for PR review: compare against HEAD
   )
   ```
3. **Resolve pending compliance checks** using the `bicameral-sync` flow
   (see below) if `sync_status.pending_compliance_checks` is non-empty.
4. Present the results:
   - **Drifted**: code has changed since the decision was recorded — needs review
   - **Pending**: decision exists but no code written yet
   - **Reflected**: code matches the decision — all good
5. For drifted decisions, quote `source_excerpt` verbatim and explain what changed.

## After the call: resolve pending compliance checks

This is the `bicameral-sync` compliance resolution flow. If
`sync_status.pending_compliance_checks` is non-empty, resolve all checks
before presenting results — **this is the only path to authoritative
`reflected`/`drifted` status.**

For each check: read `file_path` (use `code_body` preview; read file directly if
truncated), evaluate whether the code functionally implements `decision_description`,
then batch all verdicts into one call:

```
bicameral.resolve_compliance(
  phase="drift",
  flow_id="<sync_status.flow_id>",
  verdicts=[{
    decision_id:  "<check.decision_id>",
    region_id:    "<check.region_id>",
    content_hash: "<check.content_hash — echo exactly>",
    verdict:      "compliant" | "drifted" | "not_relevant",
    confidence:   "high" | "medium" | "low",
    explanation:  "<one sentence>"
  }, ...]
)
```

Verdicts: `"compliant"` = implements correctly · `"drifted"` = diverged ·
`"not_relevant"` = server retrieval mismatch (server prunes the binding).
Echo `content_hash` exactly — it's a CAS guard.

Skip when `pending_compliance_checks` is empty.

## Arguments

$ARGUMENTS — file path to check (relative to repo root)

## Example

User: "/bicameral:drift payments/processor.py"
→ Call `bicameral.drift` with `file_path="payments/processor.py"`, `use_working_tree=true`
→ If `sync_status.pending_compliance_checks` is non-empty, call `bicameral.resolve_compliance`
  with `phase="drift"` and verdicts for each check.
→ "2 decisions touch this file: (1) 'Webhook retry with backoff' — DRIFTED (code changed
  since decision). (2) 'Log payment failures' — reflected."
