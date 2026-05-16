# v0 Productization — Design Partner Dogfood (#278 Phase 4)

This is the validation milestone for #278. Phases 1–3 shipped the source
view, remove flows, and raw SurrealQL admin panel; Phase 4 is about
proving those features work in the hands of real users.

## Goal

Validate that the dashboard surfaces from Phases 1–3 enable the two
success scenarios from v0 Productization §4 without operator escalation.

## Success criteria (from #278)

1. **A PM finds a wrong decision and removes it via the dashboard, without
   escalating to the operator.** The PM:
   - sees the wrong decision in the dashboard's decision list,
   - opens the source view to verify what the ingest captured,
   - clicks "remove" on the decision,
   - completes the confirmation modal (reason + signer),
   - copies the surfaced `bicameral.remove_decision` MCP call into their
     bicameral-connected agent,
   - sees the decision render as `signoff.state="removed"` on the next
     SSE update — all without asking the operator to run anything from
     a terminal.

2. **An operator runs a SurrealQL query to investigate a stale ledger
   entry without leaving the dashboard.** The operator:
   - has started the MCP server with `BICAMERAL_ENABLE_ADMIN_PANEL=1`,
   - opens the dashboard, toggles "Advanced (raw SurrealQL panel)",
   - picks a starter query from the quickref or types their own,
   - executes in read mode, inspects the result rows,
   - never opens a separate terminal or `surreal sql` session.

## Counting

To slice the event log by design partner, start the MCP server with:

```bash
BICAMERAL_DOGFOOD_LABEL=partner-acme BICAMERAL_ENABLE_ADMIN_PANEL=1 \
  python -m bicameral_mcp
```

Every event emitted by the Phase 1–3 surfaces gains a `dogfood_label`
field with the env value. Count hits via `jq`:

```bash
# Scenario 1 — PM removes a wrong decision via dashboard
jq -c 'select(.event_type=="decision_removed.completed" and .payload.dogfood_label=="partner-acme")' \
  .bicameral/events/*.jsonl | wc -l

# Scenario 2 — operator runs SurrealQL from the dashboard
jq -c 'select(.event_type=="admin_query.executed" and .payload.dogfood_label=="partner-acme")' \
  .bicameral/events/*.jsonl .bicameral/events/_admin.jsonl 2>/dev/null | wc -l
```

The `_admin.jsonl` path captures admin queries in local-only mode (no
team adapter writer attached); the per-author `.jsonl` files capture
team-mode events. Reading both covers both deployment shapes.

## Threshold

At minimum, **one matching event for each scenario** from the design
partner's session within a 2-week dogfood window. Higher bars (multiple
PMs, multiple orgs, multiple sessions) are operator-tunable depending on
what you want to learn.

## What we expect to learn

- **Friction shape.** Is the typed "I accept the risk" confirmation in
  the admin panel right-sized for "I'm investigating" vs. "I'm about to
  break something"? If operators bounce off it, the friction is
  miscalibrated.
- **Removal triggers.** Do PMs reach for "remove decision" or "remove
  source"? The cascading remove_source is more powerful but harder to
  preview; if PMs default to remove_decision and forget remove_source
  exists, the dashboard should surface the source-grouped removal path
  more obviously.
- **Source-view utility.** Phase 1's source view renders the ingested
  excerpt with side-by-side decision linkage. If PMs don't open it
  before removing, the source view's role is wrong — maybe it should
  open automatically when a decision row is expanded.
- **Confirm-first vs. confirm-typed.** `remove_decision` and the admin
  panel use different confirmation shapes (single-step + reason vs.
  typed phrase). Dogfood reveals whether the difference is intentional
  or arbitrary.
- **Audit-log readability.** Operators who try to inspect their own
  dogfood metrics via `jq` are also testing whether the event-log shape
  is something humans can read. If the JSON shape requires too much
  munging, that's signal for a future "events viewer" surface.

## Rollback plan

If dogfood reveals a serious flaw in Phases 1–3, the operator can:

1. **Disable the admin panel** by removing `BICAMERAL_ENABLE_ADMIN_PANEL`
   from the server env and restarting. The route returns 404; the panel
   in the dashboard stays hidden because the UI side reads from the
   server route. No code change required.
2. **Soft-disable remove flows in the dashboard** by adding a CSS rule
   that hides `.rm-dec-btn` and `.rm-src-btn`. A 5-minute change; the
   backend tools remain reachable via MCP for power users.
3. **Revert by commit.** Phases 1–3 are stacked commits with no
   dependencies on subsequent v0 work (cross-checked against the
   integration map in `docs/CONCEPT.md`). `git revert <commit>` per
   phase is clean.

If the dogfood metric for scenario 1 is zero after a week, that's signal
to talk to the design partner about WHY they didn't use the dashboard
remove flow — the answer might be a docs gap, an onboarding gap, or a
genuine design defect. Don't assume a metric of zero means failure
until you have a conversation.

## Out of scope for Phase 4

- Reporting / aggregation UI for dogfood metrics. Operators count via
  `jq`; a reporting dashboard is a follow-up if dogfood produces enough
  signal to justify it.
- Pre-defined "test scripts" the design partner follows. Canned scripts
  defeat dogfood's purpose — we want to see what users actually do.
- Auto-collection of partner credentials or sessions. The label is
  operator-supplied at server start; no PII collection.
