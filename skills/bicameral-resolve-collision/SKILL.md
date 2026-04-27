---
name: bicameral-resolve-collision
description: Resolve a same-level conflict or context_for candidate after post-ingest history check. Call after bicameral.history shows overlap between a newly ingested decision and an existing one. Also called from preflight when unresolved_collisions are present. Dual-mode — collision (supersede/keep_both) or context-for (confirmed/rejected).
---

# Bicameral Resolve Collision

HITL (human-in-the-loop) resolution for two types of signals:

1. **Collision**: A newly ingested decision overlaps with an existing one at the same level.
   Detected by caller-LLM via `bicameral.history` after ingest (v0.9.3+) — not server keyword search.
2. **Context-for**: A newly ingested span may answer an existing `context_pending` decision.
   Human confirms or rejects the proposed link.

## When to call (v0.9.3+)

Collision detection is caller-LLM responsibility. The server no longer runs keyword-search supersession
at ingest time. The trigger flow is:

1. Call `bicameral.ingest` → decisions enter as `proposed`
2. Call `bicameral.history(feature_group=<group>)` to check for existing decisions
3. Compare newly ingested decisions against history:
   - **Cross-level match** (L1↔L2, L1↔L3, L2↔L3): auto-mechanical — parent/child pair, not
     a conflict. No action needed.
   - **Same-level, no meaningful overlap**: auto-mechanical.
   - **Same-level conflict**: call `bicameral_resolve_collision` (capped at 3 per ingest session)
4. At preflight when `PreflightResponse.unresolved_collisions` is non-empty → Collision mode (recovery)
5. After `bicameral.ingest` when `IngestResponse.context_for_candidates` is non-empty → Context-for mode

## Collision mode

```
bicameral.resolve_collision(
  new_id="decision:<id>",      # newly ingested decision (proposed)
  old_id="decision:<id>",      # existing decision it may supersede
  action="supersede"|"keep_both"
)
```

**When to supersede**: the new decision changes the same behavior as the old one — they
contradict. The old decision would mislead a coding agent if left live.

**When to keep_both**: the decisions cover different code areas, teams, or lifecycle phases
even though their descriptions overlap. Both are valid; the match was a false positive.

**What happens:**
- `supersede`: writes `new_id → supersedes → old_id` edge; marks `old_id.status='superseded'`;
  `new_id` stays as a live proposal.
- `keep_both`: no supersedes edge; both remain live proposals.

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

If a session ends before conflicts are resolved, any `collision_pending` decisions (pre-v0.9.3
ingest) remain held. They show in `bicameral_dashboard` as unresolved proposals and in
`bicameral_preflight.unresolved_collisions`.

To recover: call `bicameral.resolve_collision` with the held decision's ID at the next session.
To discard: call `bicameral.reset` scoped to that decision.

## Decision.status invariant

This tool NEVER sets `decision.status` directly. Status is derived via `project_decision_status`
(the double-entry authority) after each action. The only direct status write is
`old_id.status = 'superseded'` on supersession — which is a terminal state, not a compliance state.
