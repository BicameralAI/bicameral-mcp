---
name: bicameral-resolve-collision
description: Resolve a collision or context_for candidate surfaced by bicameral.ingest. Call after ingest when supersession_candidates or context_for_candidates are non-empty. Also called from preflight when unresolved_collisions are present. Dual-mode — collision (supersede/keep_both) or context-for (confirmed/rejected).
---

# Bicameral Resolve Collision

HITL (human-in-the-loop) resolution for two types of ingest signals:

1. **Collision**: A newly ingested decision overlaps with an existing one (detected by
   keyword search at ingest time). The new decision is held at `collision_pending` until resolved.
2. **Context-for**: A newly ingested span may answer an existing `context_pending` decision.
   Human confirms or rejects the proposed link.

## When to call

- After `bicameral.ingest` when `IngestResponse.supersession_candidates` is non-empty → Collision mode
- After `bicameral.ingest` when `IngestResponse.context_for_candidates` is non-empty → Context-for mode
- At preflight when `PreflightResponse.unresolved_collisions` is non-empty → Collision mode (recovery)

## Collision mode

```
bicameral.resolve_collision(
  new_id="decision:<id>",      # newly ingested decision (collision_pending)
  old_id="decision:<id>",      # existing decision it may supersede
  action="supersede"|"keep_both"
)
```

**When to supersede**: the new decision changes the same behavior as the old one — they
contradict. The old decision would mislead a coding agent if left live.

**When to keep_both**: the decisions cover different code areas, teams, or lifecycle phases
even though their descriptions overlap. Both are valid; the keyword search match was a false positive.

**What happens:**
- `supersede`: writes `new_id → supersedes → old_id` edge; marks `old_id.status='superseded'`;
  clears `collision_pending` on `new_id` so it enters normal flow as a live proposal.
- `keep_both`: clears `collision_pending` on `new_id`; no supersedes edge.

## Context-for mode

```
bicameral.resolve_collision(
  span_id="input_span:<id>",     # from context_for_candidates.span_id
  decision_id="decision:<id>",   # from context_for_candidates.decision_id
  confirmed=True|False
)
```

**On confirmed=True**: writes `input_span → context_for → decision` edge with `state='confirmed'`.
The decision stays `context_pending` but becomes eligible for `bicameral.ratify`.

**On confirmed=False**: writes the same edge with `state='rejected'`. Prevents re-surfacing
this span against this decision on future ingests.

**After confirming**: call `bicameral.ratify` when the business context is fully resolved.
Preflight surfaces context_pending decisions with ≥1 confirmed edge as "ready for ratification."

## Session-drop recovery

If a session ends before `bicameral.resolve_collision` is called, the collision-held decision
remains at `status='proposal'` (signoff.state='collision_pending') indefinitely. It shows in
`bicameral_dashboard` as an unresolved proposal and in `bicameral_preflight.unresolved_collisions`.

To recover: call `bicameral.resolve_collision` with the held decision's ID at the next session.
To discard: call `bicameral.reset` scoped to that decision.

## Decision.status invariant

This tool NEVER sets `decision.status` directly. Status is derived via `project_decision_status`
(the double-entry authority) after each action. The only direct status write is
`old_id.status = 'superseded'` on supersession — which is a terminal state, not a compliance state.
