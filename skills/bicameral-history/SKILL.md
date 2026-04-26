---
name: bicameral-history
description: Read-only dump of the full decision ledger. Fires on "show the decision history", "list all decisions", "what's in the ledger", "show me everything tracked", "give me the full decision list". Returns decisions grouped by feature area with sources, code grounding, and status.
---

# Bicameral History

Returns a read-only snapshot of everything in the decision ledger, grouped
by feature area, in a shape the dashboard and `/decisions` page can consume
directly.

## When to fire

- *"show the decision history"*
- *"list all decisions"*
- *"what's in the ledger"*
- *"show me everything tracked"*
- *"give me the full decision list"*
- *"what decisions have been recorded"*

## When NOT to fire

- Implementation verbs ("add", "build", "implement") → use `bicameral-preflight`
- Ingest / transcript phrasing → use `bicameral-ingest`
- Drift or drift-by-file questions → out of wedge

## Tool call

```
bicameral.history(
  feature_filter="<optional substring>",   # narrow to one feature
  include_superseded=True,                 # default: include superseded
  as_of="<git-ref>",                       # default: HEAD
)
```

## How to present

Group decisions by `HistoryFeature`. For each group:

1. **Header**: `FEATURE NAME  Nreflected  Ndrifted  Nungrounded  Nsuperseded`
   - Lead with features that have drifted or ungrounded decisions.
2. **Decisions in the group** — one row per decision:
   - `✓` = reflected, `⚠` = drifted, `○` = ungrounded, `~` = discovered, `—` = superseded
   - Include `sources`, `fulfillment.file_path:start_line`, and `drift_evidence` when present.

When `truncated=True`, note "Showing 50 of N features — use `feature_filter` to drill in."

## After rendering — surface unratified proposals

After the history table, scan the rendered decisions for any whose
`signoff.state == "proposed"` (i.e. not yet ratified). Group them by
feature area and present a single ratify prompt:

```
⚪ Unratified proposals in: <Feature A>, <Feature B>, <Feature C>
   Drift tracking is paused on these until ratified.
   Ratify now? [Y/n or pick features: A C]  ›
```

- If the user confirms all or a subset, call `bicameral.ratify` for
  each decision in the confirmed features (same call as
  `bicameral-ingest` step 7).
- If they decline, note it inline and move on — never ask twice in
  the same session.
- **Silent when there are no proposals.** Never say "nothing to
  ratify." The empty path is always silent.

This is the canonical ratification surface. `bicameral-ingest` and
`bicameral-capture-corrections` both leave decisions as proposals
deliberately — history is where the user reviews and ratifies in
bulk, rather than being asked at the end of every ingest.

## Status badges

| Status | Badge | Meaning |
|---|---|---|
| reflected | ✓ | Code matches the recorded decision |
| drifted | ⚠ | Code diverged from the recorded decision |
| ungrounded | ○ | Decision tracked but no code region found |
| discovered | ~ | Code implies a decision that was never recorded |
| superseded | — | Replaced by a later decision |
| proposed | ⚪ | Ingested but not yet ratified; drift tracking paused |

**Note on ephemeral commits**: when verdicts were recorded from a feature branch
commit (not yet in the authoritative branch), they are tagged `ephemeral: true`.
Status (`drifted`/`reflected`) is still computed from these verdicts — they represent
the live branch state. The dashboard shows them with a branch-delta indicator.
