---
name: bicameral-remove-decision
description: Hard-delete a wrong decision via the `bicameral.remove_decision` tool — physically removes the row + all edges + compliance_check cache rows. A `decision_removed.completed` event records the full pre-deletion snapshot in the event journal (the "soft audit trail" — see decision:i4wafafzowm3ai5eyhgs). Reason is required. Idempotent (missing → no-op). To retain a persistent negative signal, use supersession instead.
---

# Bicameral Remove Decision

Hard-delete a wrong decision via the `bicameral.remove_decision` tool. The decision row is physically removed; all references (binds_to / yields / supersedes / context_for / about edges + the compliance_check verdict cache for this decision) are cleaned up; child decisions whose `parent_decision_id` pointed at the removed id are orphaned cleanly to root-level. The act of removal is recorded as a `decision_removed.completed` event with the full pre-deletion snapshot — recoverable from the journal alone.

As of v0.15.x (decision:i4wafafzowm3ai5eyhgs), there is no soft-delete / tombstone state. The previous `signoff.state = "removed"` model was retired because tombstones over-indexed on the negative-signal use case while making janitorial cleanup friction-heavy (removed rows surfaced in preflight, occupied dashboard slots, and got re-bound by drift sweeps).

## When to use

- Operator finds a decision that was extracted in error (transcript misread, hallucination, wrong ingest target) and wants to correct the ledger without nuking everything.
- A test fixture / sample payload was ingested by accident during development and needs to come out cleanly without taking other decisions with it.
- A pre-ratification proposal turned out to be incoherent / unhelpful and should be erased rather than preserved as a tombstone.

## When NOT to use

- **For decisions you want to evolve past.** Use `bicameral.resolve_collision action=supersede` instead. Supersession preserves lineage (the new decision points at the old one) and produces an explicit record of WHY the team changed its mind. That record is the right negative signal for future agents — far more useful than a tombstone with no superseding intent.
- **For GDPR right-to-erasure of regulated PII.** Out of scope. Use `bicameral.remove_source` for span-level erasure that cascades through decisions, or run the operator-facing PII archive erasure flow.
- **For hiding a decision.** Every removal writes an audit event with `signer` + `reason` + full snapshot. There is no quiet remove.
- **For undoing a removal.** The event journal already records that the removal happened. If the removal was a mistake, re-ingest the decision (the canonical text lives in the event payload's `snapshot`).

## Mandatory verification

Before calling `bicameral.remove_decision`:

1. **Read the decision** via `bicameral.history` or the dashboard. Confirm `decision_id` matches the one you intend to remove. The dashboard surface is the human-readable cross-reference.
2. **Compose a non-trivial reason.** A bare "wrong" is technically accepted but unhelpful. Future-you (or a future operator) reads this reason in the event journal to understand WHY the entry was removed. Recommended shape: `<symptom> — <cause> — <action taken>` (e.g., "Duplicate of decision:abc — transcript was ingested twice — keeping the earlier one").
3. **Consider supersession first.** If the removed decision should warn future agents away from a wrong idea, supersession is the better tool — it preserves the historical lineage AND captures the contradicting intent as a separate, ratifiable decision.

## Format

```json
{
  "name": "bicameral.remove_decision",
  "arguments": {
    "decision_id": "decision:abc123",
    "signer": "your-email-or-agent-id",
    "reason": "Duplicate of decision:def456 — transcript ingested twice."
  }
}
```

## Handler-side enforcement

The handler rejects calls with:
- empty / whitespace-only `reason` → `ValueError("remove_decision requires a non-empty 'reason' …")`

Unknown `decision_id` is NOT an error — the handler returns `was_new=False` (idempotent no-op). The matching event in the journal is the canonical record of any prior removal.

## What the tool deletes

| Removed | Cleaned up | Orphaned cleanly |
|---|---|---|
| `decision:<id>` row | `binds_to WHERE in = <id>` | child `decision.parent_decision_id` set to NONE |
| | `yields WHERE out = <id>` | (children become root-level) |
| | `supersedes WHERE in = <id> OR out = <id>` | |
| | `context_for WHERE out = <id>` | |
| | `about WHERE in = <id>` | |
| | `compliance_check WHERE decision_id = <id>` | |

`input_span` rows are NOT touched — they may yield other decisions. Use `bicameral.remove_source` if you also want to erase the source span and cascade through every decision it produced.

## Response shape

```json
{
  "decision_id": "decision:abc123",
  "was_new": true,
  "event_logged": true,
  "removed_at": "2026-05-15T22:15:00.000000+00:00",
  "previous_state": "ratified",
  "reason": "Duplicate of decision:def456 — transcript ingested twice."
}
```

| Field | Meaning |
|---|---|
| `was_new` | `true` iff this call physically deleted a row. `false` on the idempotent no-op path. |
| `event_logged` | `true` iff a `decision_removed.completed` event was emitted (team mode with attached writer). |
| `removed_at` | ISO timestamp recorded on this removal. `null` on the no-op path. |
| `previous_state` | `signoff.state` immediately before delete (e.g. `"ratified"`, `"proposed"`, `null` if unsigned). |
| `reason` | Echo of the audit reason. |

## Audit trail

Every successful removal appends one event to the local event log:

```
.bicameral/events/<author>.jsonl
{
  "event_type":"decision_removed.completed",
  "author":"…",
  "timestamp":"…",
  "payload":{
    "decision_id":"decision:abc123",
    "signer":"…",
    "reason":"…",
    "removed_at":"…",
    "session_id":"…",
    "previous_state":"…",
    "source_commit_ref":"…",
    "snapshot":{
      "description":"<full decision text>",
      "status":"…",
      "source_type":"…",
      "source_ref":"…",
      "decision_level":"…",
      "parent_decision_id":"…",
      "feature_group":"…",
      "governance":{…},
      "signoff":{…},
      "created_at":"…",
      "updated_at":"…"
    }
  }
}
```

The full pre-deletion snapshot lives in `payload.snapshot` so the action is recoverable from the journal alone — the "soft audit trail" that replaces the tombstone row. In team mode, the event is replicated through the shared event-log backend.

## After removal

- The decision row is gone. `bicameral.history` and the dashboard will no longer surface it.
- `bicameral.preflight` won't surface it as a negative signal (use supersession for that effect).
- Bound code regions remain — they may be bound to other decisions; orphaned regions are harmless. To prune them, use a separate cleanup pass.

## Anti-patterns — REJECT these

| Anti-pattern | Why it fails |
|---|---|
| Using `remove_decision` as a substitute for supersession | Removal severs lineage; supersession preserves it. Pick supersession when the new decision evolves the old; pick removal when the old decision should never have existed. |
| Submitting an empty or single-word reason | The handler rejects empty/whitespace reasons; single-word reasons technically pass but defeat the audit-trail purpose. Reviewers reading the event log months later need context. |
| Calling `remove_decision` then expecting to call something to undo it | The row is gone. To restore, re-ingest the decision (the canonical text is in the event payload's `snapshot` field). |
| Expecting `remove_decision` to also remove the source span | It doesn't — only the decision row + its edges + cache. Use `bicameral.remove_source` if you want to erase the span and cascade-delete every decision it yielded. |
