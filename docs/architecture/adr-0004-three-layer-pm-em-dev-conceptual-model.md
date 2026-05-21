# ADR-0004: Three-Layer PM / EM / Dev Conceptual Model

**Status:** Accepted for planning  
**Date:** 2026-05-21  
**Related:** `docs/design/dashboard-v2-comprehensive-design.md`, `docs/design/dashboard-v2-conceptual-architecture-amendment.md`

## Context

The Dashboard v2 design already defines an application information architecture: Pulse, Ledger, Ratification, Drift, Sources, Audit, Integrations, and Settings.

A newer conceptual architecture diagram introduces a stronger product model:

> Bicameral is the walkie-talkie for software teams.

The diagram frames Bicameral as coordination infrastructure across three owners:

- PM / Product Owner.
- EM / Engineering Manager.
- Dev / Developer.

It also introduces three layers:

- Decision Ledger.
- Dependency Layer.
- Grounding Layer.

This model is strong enough to influence the Dashboard v2 design, role model, copy, and product documentation.

## Decision

Adopt the three-layer PM / EM / Dev conceptual model as the product mental model beneath Dashboard v2.

The application navigation remains unchanged for MVP:

```text
Pulse
Ledger
Ratification
Drift
Sources
Audit
Integrations
Settings
```

The conceptual model informs these views rather than replacing them.

## Three Layers

### Decision Ledger

Owner lens: PM / Product Owner.

Purpose:

- Human signoff.
- Decision authority.
- Proposed / ratified / rejected / superseded state.
- Artifact compliance state such as ungrounded, reflected, drifted.

Corrected ownership language:

> PM owns ratification authority over product decisions. Bicameral owns the canonical ledger. Sources and agents may propose entries, but human signoff determines decision authority.

### Dependency Layer

Owner lens: EM / Engineering Manager.

Purpose:

- Blast-radius detection.
- Scope creep detection.
- Requirement gap routing.
- Dependency graph or K-hop awareness.
- Routing surfaced issues between PM and Dev.

### Grounding Layer

Owner lens: Dev / Developer.

Purpose:

- Code and artifact grounding.
- File/symbol/commit/PR evidence.
- IDE plugin input.
- Agent-session capture.
- Preventing hallucinated implementation context.

## Relationship To Dashboard Views

| View | Conceptual support |
|---|---|
| Pulse | Summarizes decision, dependency, grounding, and team freshness state. |
| Ledger | Canonical decision record and Decision Ledger layer. |
| Ratification | PM/Product Owner decision authority workflow. |
| Drift | Grounding and dependency mismatch evidence. |
| Sources | Feeds into all layers. |
| Audit | Operational and security event trail. |
| Integrations | Source and ratification surface configuration. |
| Settings | Identity, sync, preferences, themes, accessibility, credentials, diagnostics. |

A future `Dependency Map` view may be added when the underlying data can support reliable dependency routing, impact analysis, and owner handoff visualization.

## Rationale

The three-layer model makes Bicameral easier to understand:

- PMs need to know what decisions require product authority.
- EMs need to know where decisions create dependency or scope risk.
- Developers need grounded evidence tied to real code and artifacts.

This model also gives the product a clearer market position than a generic decision ledger.

## Consequences

Positive:

- Stronger product story.
- Clearer owner lenses.
- Better mapping from integrations to user value.
- Better framing for Pulse and Drift.
- More memorable brand language.

Tradeoffs:

- Must avoid over-literal walkie-talkie UI language.
- Must avoid implying PM exclusively owns every ledger entry.
- Must overlay Google Drive freshness state so ideal flows are not confused with current team certainty.
- May eventually require a dedicated Dependency Map view.

## UI Rules

1. Use the walkie-talkie metaphor for positioning, onboarding, and explainers, not literal dashboard interaction labels.
2. Keep the dashboard operational and calm.
3. Show PM, EM, and Dev owner lenses where they clarify responsibility.
4. Keep Ledger as the canonical record.
5. Add dependency/blast-radius data into Pulse and Drift before creating a top-level Dependency Map route.
6. Always display team freshness state where shared decisions or routing are involved.

## Acceptance Criteria

- Design documentation includes the three-layer model.
- Design documentation maps PM, EM, and Dev to owner lenses.
- Pulse requirements include decision, dependency, grounding, and team freshness state.
- Ledger requirements include fixed, domain-agnostic decision record language.
- Drift requirements distinguish product intent drift, dependency/scope drift, and grounding/artifact drift.
- Integration settings can identify owner lens: PM, EM, Dev, or shared.
- Team Memory State overlays the model when Google Drive sync is stale/offline.
