# Dashboard v2 Conceptual Architecture Amendment

**Repository:** `Knapp-Kevin/bicameral-mcp`  
**Product:** Bicameral MCP  
**Parent document:** `docs/design/dashboard-v2-comprehensive-design.md`  
**Status:** Accepted for planning  
**Date:** 2026-05-21

## 1. Purpose

This amendment adds the stronger product mental model introduced by the latest architecture diagram:

> Bicameral is the walkie-talkie for software teams.

That phrase should not replace the Dashboard v2 application architecture. It should clarify the product story behind it.

The dashboard still needs Pulse, Ledger, Ratification, Drift, Sources, Audit, Integrations, and Settings. This amendment defines the conceptual layer model and role-ownership model that should guide those views.

In plainer terms: this document prevents the UI from becoming either a raw ledger table or a cute diagram with no controls. Two ways to lose, because software design apparently enjoys variety.

## 2. Product Mental Model

The new product metaphor is:

> Bicameral keeps PM, EM, and Dev in sync when decisions move faster than the codebase.

The dashboard should express this model through three layers and three owner roles.

### 2.1 Three layers

| Layer | Ownership | Purpose | Product meaning |
|---|---|---|---|
| Decision Ledger | PM / Product Owner | Human signoff, decision state, compliance artifact state | What was decided and whether it is ratified. |
| Dependency Layer | EM / Engineering Manager | Blast-radius detection, scope creep detection, routing, dependency awareness | Who or what is affected by a decision or drift. |
| Grounding Layer | Dev / Developer | Code and artifact grounding, implementation evidence, IDE/plugin/session input | Whether the implementation is tied to real artifacts rather than model imagination. |

### 2.2 Owner roles

The diagram introduces a more product-specific role model than generic RBAC labels.

#### PM / Product Owner

Owns:

- Decision authority.
- Ratification.
- Rejection.
- Supersession.
- Requirements gaps.
- Product source evidence.

Does not exclusively own every ledger entry. Sources, agents, developers, and integrations may propose ledger entries. PM owns final decision authority for product-relevant decisions.

#### EM / Engineering Manager

Owns:

- Dependency routing.
- Blast-radius review.
- Scope creep detection.
- Cross-team visibility.
- Risk routing between PM and Dev.
- Determining whether a surfaced issue needs product clarification or technical investigation.

#### Dev / Developer

Owns:

- Grounding evidence.
- Affected files.
- Symbols.
- Commits.
- Pull requests.
- IDE/plugin input.
- Implementation context.

## 3. Corrected Ownership Language

Do not say:

> PM owns the ledger.

That is too broad.

Use:

> PM owns ratification authority over product decisions. Bicameral owns the canonical ledger. Sources and agents may propose entries, but human signoff determines decision authority.

Do not say:

> Dev owns drift.

Use:

> Dev owns implementation evidence and grounding. Drift is a system-detected mismatch that may route to PM, EM, or Dev depending on whether the issue is product intent, dependency impact, or artifact grounding.

Do not say:

> EM owns all routing.

Use:

> EM owns dependency and blast-radius routing where cross-team or scope-impact decisions need management attention.

## 4. Relationship To Existing Dashboard IA

The existing Dashboard v2 IA remains valid. The conceptual model should map into it as follows.

| Application View | Conceptual Layer Support |
|---|---|
| Pulse | Summary across Decision Ledger, Dependency Layer, Grounding Layer, and Team Freshness. |
| Ledger | Decision Ledger layer. Canonical record of proposed, ratified, rejected, superseded, reflected, drifted, and ungrounded decisions. |
| Ratification | PM / Product Owner decision authority workflow. |
| Drift | Grounding and dependency mismatch evidence. |
| Dependency Map | Future or embedded view for blast-radius and scope-creep routing. |
| Sources | Feeds into all three layers. |
| Audit | Operational and security event trail across layers. |
| Integrations | Configuration for incoming feeds and ratification surfaces. |
| Settings | Identity, team sync, preferences, accessibility, themes, credentials, and diagnostics. |

## 5. Navigation Amendment

The current recommended navigation remains:

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

This amendment adds one future candidate:

```text
Dependency Map
```

### 5.1 MVP treatment

For the first implementation, do not add Dependency Map as a required top-level route unless the underlying data already supports it.

Instead:

- Include dependency/blast-radius cards inside Pulse.
- Include dependency evidence inside Drift.
- Include owner routing inside Ratification and Decision Detail.

### 5.2 Later treatment

Add `Dependency Map` as a top-level view when Bicameral can reliably show:

- Decision-to-file impact.
- Decision-to-team impact.
- Decision-to-feature impact.
- Scope creep signals.
- Routing recommendations.
- PM/EM/Dev owner handoffs.
- K-hop dependency graph or equivalent dependency reasoning.

## 6. Pulse Amendments

Project Pulse should summarize the three-layer model.

Recommended Pulse sections:

1. **Team Memory State**
   - Google Drive freshness.
   - Local queue.
   - Shared actions enabled/paused.

2. **Decision Ledger Health**
   - Proposed.
   - Ratified.
   - Rejected.
   - Superseded.
   - Ungrounded.
   - Reflected.
   - Drifted.

3. **Dependency Pulse**
   - Scope creep signals.
   - Blast-radius warnings.
   - Affected features or owners.
   - EM-routed issues.

4. **Grounding Pulse**
   - Code/artifact links.
   - Drift evidence.
   - Ungrounded decisions.
   - IDE/plugin/session sources.

5. **Suggested Next Move**
   - Review ratification.
   - Inspect drift.
   - Explain scope creep.
   - Explore technical constraints.
   - Reconnect Drive.

## 7. Ledger Amendments

The Ledger view should explicitly reflect the Decision Ledger layer from the diagram.

Recommended Ledger description:

> The Ledger is Bicameral's fixed, domain-agnostic decision record. It tracks human signoff and artifact compliance state across proposed, ratified, rejected, ungrounded, reflected, drifted, and superseded decisions.

Ledger filters should include:

- Signoff state: proposed, ratified, rejected, superseded.
- Artifact state: ungrounded, reflected, drifted.
- Source: Slack, PRD, transcript, Jira, Linear, GitHub, Notion, Google Drive, manual.
- Owner lens: PM, EM, Dev, unassigned.
- Level: L1/L2/L3.
- Freshness: local-only, synced, queued, stale.

## 8. Ratification Amendments

Ratification should be framed as PM/Product Owner authority, while still allowing EM/Dev context.

Decision cards should show:

- Decision text.
- Proposed source.
- Required owner: PM, EM, Dev, or shared.
- Why the owner is required.
- Related dependency impact.
- Related grounding evidence.
- Team freshness state.
- Actions available by capability and freshness.

Recommended owner-action copy:

- PM: `Ratify product intent`.
- EM: `Route dependency impact`.
- Dev: `Add grounding evidence`.

## 9. Drift and Dependency Amendments

Drift should not be only a code mismatch list. It should show whether the issue is:

1. Product intent drift.
2. Dependency/scope drift.
3. Grounding/artifact drift.

Recommended labels:

- `Requirement gap surfaced`.
- `Scope creep surfaced`.
- `Implementation drift surfaced`.
- `Grounding missing`.
- `Artifact changed`.
- `Router action required`.

Each drift card should include:

- Origin layer.
- Destination owner.
- Why the route was selected.
- Evidence.
- Suggested next action.

## 10. Sources and Integrations Amendments

The diagram groups incoming information as PM integrations and Dev integrations. The app should preserve the broader source category model while adding owner lens metadata.

### 10.1 PM-facing sources

Examples:

- Transcript.
- Slack / PRD.
- Jira.
- Linear.
- Notion.
- Google Drive Docs.

### 10.2 Dev-facing sources

Examples:

- Agent session.
- Git commit.
- IDE plugin.
- GitHub PR.
- Local files.
- Artifact locator / code grounding.

### 10.3 EM-facing signals

Examples:

- Scope creep detection.
- Blast-radius dependency graph.
- Requirement gap surfaced.
- Drift surfaced.
- Router ownership.

Integration configuration should support this owner lens:

```ts
type SourceOwnerLens = "pm" | "em" | "dev" | "shared";
```

## 11. Team Sync Overlay Requirement

The diagram shows ideal active flows. The real product currently uses Google Drive as the team file-share substrate.

Every layer must be overlaid with team freshness state.

Required Team Memory State ribbon/card:

```text
Team Memory State
Google Drive: current / stale / offline / auth needed / conflict
Local queue: N events
Shared actions: enabled / paused
Last confirmed team sync: timestamp
```

When stale/offline:

- Decision Ledger remains viewable from local state.
- Dependency routing must be marked as local/stale if remote updates may be missing.
- Grounding evidence remains locally useful.
- Shared ratification is paused until Drive is current.
- Local proposals may queue.

## 12. Brand and Marketing Guidance

`The Walkie-Talkie for Software Teams` is a strong product metaphor for external-facing material.

Best uses:

- Website hero.
- README positioning.
- Product onboarding.
- Empty states.
- Explainer diagrams.
- Pitch deck.

Use sparingly inside the dashboard.

Good dashboard copy:

> Bicameral keeps PM, EM, and Dev aligned when decisions move faster than the codebase.

Avoid dashboard copy like:

> Walkie-Talkie status: transmitting.

The product should feel calm and operational, not like a toy radio got promoted to CTO.

## 13. Accessibility and Diagram Translation

If the conceptual diagram becomes part of the app or docs site, it must have a text equivalent.

Required accessible description:

> Bicameral has three layers. The Decision Ledger records human signoff and artifact compliance states. The Dependency Layer detects blast radius and scope creep, routing surfaced gaps through the EM. The Grounding Layer links implementation artifacts and code evidence through developer-owned integrations. PM integrations feed decisions into the ledger, Dev integrations feed grounding evidence into the grounding layer, and the EM router coordinates requirement gaps and drift between the two sides.

Do not rely on arrows, colors, or layout alone.

## 14. Acceptance Criteria Amendments

Add these to the Dashboard v2 acceptance criteria:

1. The UI or documentation includes the three-layer conceptual model: Decision Ledger, Dependency Layer, Grounding Layer.
2. The UI distinguishes PM, EM, and Dev owner lenses.
3. Pulse summarizes decision, dependency, grounding, and team freshness state.
4. Ledger preserves the current detailed view and labels it as the fixed, domain-agnostic decision record.
5. Drift distinguishes product intent drift, dependency/scope drift, and grounding/artifact drift.
6. Integration settings can tag sources by owner lens: PM, EM, Dev, or shared.
7. Team Memory State overlays the conceptual model so users do not confuse ideal flow with current Drive-backed freshness.
8. The Walkie-Talkie metaphor is used for positioning, not as a literal dashboard interaction model.

## 15. Final Amendment Thesis

The dashboard should not merely show a list of decisions. It should show the operating system of team alignment:

- PM controls decision authority.
- EM controls dependency routing and blast-radius awareness.
- Dev controls grounding evidence.
- Bicameral preserves the ledger and routes drift between the layers.
- Google Drive sync defines whether shared team memory is current or stale.

That is the product story worth designing around.
