---
name: bicameral-remove-source
description: Hard-delete an input_span row + cascade-soft-delete every decision derived from it via `bicameral.remove_source`. Confirm-first (dry-run returns the cascade plan). Reason is required. Audit-logged with the full pre-deletion span content in the source_removed.completed event payload. Idempotent on missing spans. No restore (write a superseding decision if you need to reverse).
---

# Bicameral Remove Source

Hard-delete an input_span row and cascade-soft-delete every decision derived from it. Bridges the gap between accepting a bad source forever and running `bicameral.reset` (full wipe).

## When to use

- Operator finds a bad source — typo-ridden transcript, accidental Slack ingest, wrong document version, hallucinated content from a misconfigured agent — and wants to retract every decision that was derived from it.
- Multiple decisions on the same source are all wrong for the same root cause (the source itself was bad). Removing the source is one atomic operation; remove_decision on each derived decision is N operations and easy to skip one.
- Cleanup of test ingest during development that polluted the ledger.

## When NOT to use

- When only one decision out of many on the source is wrong — use `bicameral.remove_decision` on the specific decision instead. `remove_source` cascades unconditionally; a multi-source decision will be soft-deleted even if its other sources are still valid.
- For GDPR right-to-erasure — out of scope per `issue_221_design_directive.md`. The append-only event log retains the full pre-deletion span content; this is operator-correction, not legal-compliance erasure.
- For undoing a removal — there is no restore. The event log carries the audit trail; manual SurrealQL re-ingest is the recovery path if needed.

## Mandatory verification

1. **Dry-run first.** ALWAYS call with `confirm=false` first. The response is a `RemoveSourcePlan` with the full input_span content (verify it's the right one) and the list of every decision id that will be cascade-soft-deleted (verify the blast radius matches your intent).
2. **Verify the cascade size.** If `decision_ids` has more entries than you expected, STOP. A surprising cascade is a signal that the source is more load-bearing than you realized; investigate the unexpected decisions before confirming.
3. **Compose a non-trivial reason.** The reason is persisted in the source_removed.completed event payload. Future reviewers (or future-you) read it to understand the operator's intent. Recommended shape: `<symptom> — <cause> — <action taken>` (e.g., "Garbled OCR transcript — wrong PDF version was ingested — replaced by clean version in next ingest pass").
4. **Re-invoke with confirm=true.** Only after dry-run inspection.

## Format

Dry-run:

```json
{
  "name": "bicameral.remove_source",
  "arguments": {
    "span_id": "input_span:abc123",
    "signer": "your-email-or-agent-id",
    "reason": "Garbled OCR transcript — wrong PDF version ingested",
    "confirm": false
  }
}
```

Confirm:

```json
{
  "name": "bicameral.remove_source",
  "arguments": {
    "span_id": "input_span:abc123",
    "signer": "your-email-or-agent-id",
    "reason": "Garbled OCR transcript — wrong PDF version ingested",
    "confirm": true
  }
}
```

## Handler-side enforcement

- Empty `reason` → `ValueError`.
- Unknown `span_id` is idempotent: dry-run returns `span_existed=false` with empty `decision_ids`; confirm returns `span_existed=false` with `event_logged=false`. No exception.
- `confirm=true` performs three atomic operations:
  1. For each derived decision, UPDATE signoff to `{state: "removed", removed_by_source: <span_id>, reason: ..., signer: ..., removed_at: ..., previous_state: ...}` and re-project status.
  2. DELETE all `yields` edges with `in = <span_id>`.
  3. DELETE the input_span row itself.
- One `source_removed.completed` event is emitted (when adapter is in team mode) covering the entire cascade — NOT one event per decision. Operator's intent is "remove this source"; the cascade is a derived effect.

## Audit trail

Every successful confirm appends one event:

```
.bicameral/events/<author>.jsonl
{
  "event_type": "source_removed.completed",
  "author": "...",
  "timestamp": "...",
  "payload": {
    "span_id": "input_span:abc123",
    "input_span_content": {
      "text": "(full pre-deletion text)",
      "source_ref": "...",
      "source_type": "...",
      "meeting_date": "...",
      "speakers": [...],
      "created_at": "..."
    },
    "cascaded_decision_ids": ["decision:xxx", "decision:yyy"],
    "signer": "...",
    "reason": "...",
    "removed_at": "..."
  }
}
```

The `input_span_content` block is the recoverability anchor. If the operator made a mistake, the full source content survives in the event log and can be re-ingested manually.

## After removal

- The input_span row is gone from SurrealDB. `bicameral.history` no longer renders the source for any decision.
- Cascaded decisions carry `signoff.state="removed"` with `signoff.removed_by_source=<span_id>` as a back-pointer. The pointed-to span no longer exists but the back-pointer preserves the audit relationship.
- Agents that consult the ledger see removed decisions as negative signals.

## Anti-patterns — REJECT these

| Anti-pattern | Why it fails |
|---|---|
| Skipping the dry-run | The cascade is unconditional; a source with 100 derived decisions soft-deletes all 100. Without inspecting the plan you cannot know the blast radius until after you've fired. |
| Using remove_source for a single wrong decision | Use `remove_decision` instead. `remove_source` is for the case where the SOURCE is the root cause. |
| Submitting an empty or single-word reason | The handler rejects empty reasons; single-word reasons technically pass but defeat the audit-trail purpose. The event payload's reason is permanent. |
| Expecting an unremove / restore call | No unremove exists. The event log captures the full span content; manual re-ingest is the recovery path. |
