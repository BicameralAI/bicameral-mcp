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

## Before rendering — handle pending compliance checks

Check `response._pending_compliance_checks`. If non-empty, a new commit was
detected and compliance is unresolved — **status will be wrong until you resolve
it.** Use the `bicameral-sync` compliance resolution flow:

For each check: read `file_path`, evaluate code against `decision_description`,
batch all verdicts into one call:

```
bicameral.resolve_compliance(
  phase="drift",
  flow_id="<response._pending_flow_id>",
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

Then render the history table as normal.

## How to present

Group decisions by `HistoryFeature`. For each group:

1. **Header**: `FEATURE NAME  Nreflected  Ndrifted  Nungrounded  Nsuperseded`
   - Lead with features that have drifted or ungrounded decisions.
2. **Decisions in the group** — render as a tree, not a flat list:
   - Use `parent_decision_id` to identify L2/L3 decisions. Render L1 roots first (sorted by status priority), then indent each L2 child under its L1 parent, and L3 children under their L2 parent.
   - Indent L2 by 2 spaces + `[L2]` prefix. Indent L3 by 4 spaces + `[L3]` prefix. L1 decisions get an `[L1]` prefix.
   - Left symbol = code status: `✓` reflected · `⚠` drifted · `○` ungrounded · `~` ungrounded + AI-surfaced
   - Right symbol = signoff: `✓ date` ratified · `○` proposed · `~` AI-surfaced · `✕` rejected · `⚑` needs context · `—` superseded
   - Include `sources`, `fulfillments[].file_path:start_line`, and `drift_evidence` when present.
   - If `decision_level` is absent, treat the decision as L1 (flat/legacy).

Example layout:
```
FEATURE NAME   3 reflected   1 drifted   2 ungrounded

[L1] ✓ top-level decision                              ✓ 2026-04-20
       file.py:42  Source: meeting-2026-04-01
     [L2]   ✓ child decision (indented)                ✓ 2026-04-20
             file.py:88
     [L2]   ⚠ drifted child                            ✓ 2026-03-15
             Drift: content changed since last sync
[L1] ○ ungrounded top-level                            ○
```

When `truncated=True`, note "Showing 50 of N features — use `feature_filter` to drill in."

## After rendering — surface unratified proposals

After the history table, scan the rendered decisions for any whose
`signoff.state == "proposed"` (i.e. not yet ratified). Group them by
feature area and present a single ratify prompt:

```
○ Unratified proposals in: <Feature A>, <Feature B>, <Feature C>
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

## Badges

**Status** (code-compliance — left column):

| Badge | Meaning |
|---|---|
| ✓ | reflected — code matches |
| ⚠ | drifted — code diverged |
| ○ | ungrounded — no code region yet |
| ~ | ungrounded + AI-surfaced (`signoff.discovered=true`) |

**Signoff** (human-approval — right column):

| Badge | Meaning |
|---|---|
| ✓ date | ratified |
| ○ | proposed (human-ingested) |
| ~ | proposed, AI-surfaced |
| ✕ | rejected |
| ⚑ | needs context |
| — | superseded |

**Note on ephemeral commits**: when a decision's current status was determined by a
feature-branch commit (not yet in the authoritative branch), `ephemeral: true` is set
on the `HistoryDecision`. Status (`drifted`/`reflected`) is still valid — it represents
the live branch state. The dashboard renders a `⎇` badge in the state cell with tooltip
"Status from feature branch — not yet verified on main". In your text rendering, append
`⎇` after the status badge for ephemeral decisions.
