---
name: bicameral-remove-decision
description: Soft-delete a wrong decision via the `bicameral.remove_decision` tool. The decision row stays in the ledger with `signoff.state = "removed"` plus a reason — agents consult removed decisions as negative signals to avoid re-introducing the same mistake. Reason is required. Idempotent. No unremove (write a superseding decision if you need to reverse).
---

# Bicameral Remove Decision

Soft-delete a wrong decision via the `bicameral.remove_decision` tool. Bridges the gap between accepting a wrong decision in the ledger forever and running `bicameral.reset` (full wipe).

## When to use

- Operator finds a decision that was extracted in error (transcript misread, hallucination, wrong ingest target) and wants to correct the ledger without nuking everything.
- An agent ratified a decision and product owner wants to retract — write a superseding decision is the preferred path, but `remove_decision` is the right tool when the original is so wrong it should never have been ratified (rather than evolved past).
- A test decision was ingested by accident during development and needs to come out without taking everything else with it.

## When NOT to use

- For GDPR right-to-erasure of PII — out of scope per `issue_221_design_directive.md`. The append-only ledger keeps the row; the removed-state is operator-correction, not legal-compliance erasure.
- For decisions you want to evolve past — write a new decision that supersedes (via `bicameral.resolve_collision action=supersede`). Supersession preserves the historical lineage; removal severs it.
- For hiding a decision from the UI without an audit trail — every removal writes an audit event. There is no quiet remove.
- For undoing a removal — there is no unremove. If a removal was a mistake, write a new decision that captures the correct intent.

## Mandatory verification

Before calling `bicameral.remove_decision`:

1. **Read the decision** via `bicameral.history` or the dashboard. Confirm `decision_id` matches the one you intend to remove. Decision IDs are deterministic (UUIDv5) but the dashboard surface is the human-readable cross-reference.
2. **Compose a non-trivial reason.** A bare "wrong" is acceptable but unhelpful. Future-you (or a future operator) reads this reason to understand WHY the entry was removed. Recommended shape: `<symptom> — <cause> — <action taken>` (e.g., "Duplicate of decision:abc — transcript was ingested twice — keep the earlier one").
3. **Verify idempotency expectations.** If `signoff.state` is already `"removed"`, the second call returns `was_new=false` and does not emit a duplicate event. This is intentional: re-running the tool is safe.

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
- empty `reason` → `ValueError("remove_decision requires a non-empty 'reason' …")`
- unknown `decision_id` → `ValueError("No decision row for {decision_id}")`

State changes are written to the ledger via the same `UPDATE … SET signoff` path that `bicameral.ratify` uses, plus a re-projection of `status` (matches the post-ratify projection contract).

## Audit trail

Every successful removal appends one event to the local event log:

```
.bicameral/events/<author>.jsonl
{"event_type":"decision_removed.completed","author":"…","timestamp":"…","payload":{"decision_id":"…","signoff":{"state":"removed","signer":"…","reason":"…","previous_state":"…","removed_at":"…",…}}}
```

The event payload carries `previous_state` so reviewers can see what state the decision was in before removal (e.g., `ratified → removed` is a much stronger signal than `proposed → removed`).

In team mode, the event is also replicated through the shared event-log backend (see `bicameral.history` skill for team-mode semantics).

## After removal

- The decision row remains in the ledger. `bicameral.history` and the dashboard render it with a "removed" visual indicator (Phase 2 of #278 dashboard work).
- Agents that consult the ledger see `signoff.state="removed"` and treat the decision as a negative signal: do not re-introduce the same intent unless you can defend it (write a superseding decision).
- The original signoff state (if any) is preserved in `signoff.previous_state` for forensic review.

## Anti-patterns — REJECT these

| Anti-pattern | Why it fails |
|---|---|
| Calling `remove_decision` to "hide" an embarrassing decision | Every removal is audit-logged with signer + reason; there is no hidden removal. |
| Using `remove_decision` as a substitute for `bicameral.resolve_collision action=supersede` | Supersession preserves lineage and history; removal severs them. Pick supersession when the new decision evolves the old one; pick removal when the old decision should never have existed. |
| Submitting an empty or single-word reason | The handler rejects empty reasons; single-word reasons technically pass but defeat the audit-trail purpose. Reviewers reading the event log months later need context. |
| Calling `remove_decision` then expecting to call something to undo it | No unremove exists. The append-only event log makes "undo" semantically incoherent — the removal event happened. Write a new decision that captures the correct intent. |
