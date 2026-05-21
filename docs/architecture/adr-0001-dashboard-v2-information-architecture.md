# ADR-0001: Dashboard v2 Information Architecture

**Status:** Accepted for planning  
**Date:** 2026-05-21  
**Related:** `docs/design/dashboard-v2-comprehensive-design.md`

## Context

The current Bicameral dashboard exposes useful decision information but presents it primarily as a ledger-oriented operational view. Upstream work on Project Pulse establishes a stronger product direction: Bicameral should feel like the project memory is awake, surfacing health, needs attention, recently learned decisions, and suggested next actions.

However, the existing ledger view remains valuable. It contains the detailed canonical decision history and should not be discarded.

## Decision

Adopt a multi-view dashboard information architecture:

1. **Pulse**: default landing view for situational awareness.
2. **Ledger**: canonical decision record retaining the existing detailed view.
3. **Ratification**: workflow surface for approving, rejecting, requesting changes, and superseding decisions.
4. **Drift**: implementation mismatch and evidence surface.
5. **Sources**: ingest visibility grouped by connector/source type.
6. **Audit**: operational and security event trail.
7. **Integrations**: connection and source configuration center.
8. **Settings**: profile, team sync, themes, accessibility, privacy, diagnostics, and advanced controls.

## Rationale

This structure separates distinct user jobs:

- Pulse answers what needs attention.
- Ledger answers what the canonical decision record contains.
- Ratification answers what needs human judgment.
- Drift answers where implementation diverged.
- Sources answer where decisions are coming from.
- Audit answers what the system did and why.
- Integrations answer how sources are configured.
- Settings answer how the product behaves for the local user and team.

Combining these into one dashboard would create a dense, confusing UI. Replacing the Ledger would throw away an already useful audit-oriented view.

## Consequences

Positive:

- Preserves existing functionality.
- Gives new users a clearer landing experience.
- Gives power users a detailed Ledger view.
- Creates room for integrations, audit, and settings expansion.

Tradeoffs:

- Requires navigation and route structure.
- Requires shared decision-detail components across Ledger, Ratification, and Drift.
- Requires careful consistency between Pulse summary data and Ledger canonical data.

## Acceptance Criteria

- Pulse is default dashboard route.
- Existing detailed decision view remains accessible as Ledger.
- Ledger is explicitly described as the canonical decision record.
- Ratification and Drift are separate views, not hidden filters inside Ledger.
- Sources and Audit are separate concepts in the UI.
