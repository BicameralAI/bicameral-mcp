# Bicameral Dashboard v2 Comprehensive Design Specification

**Repository:** `Knapp-Kevin/bicameral-mcp`  
**Product:** Bicameral MCP  
**Document type:** Product design and implementation specification  
**Status:** Ready for planning and phased implementation  
**Date:** 2026-05-21

## 1. Executive Summary

Bicameral MCP already has a functional local dashboard and a strong product thesis: AI agents ship code fast, but they forget what teams agreed. The dashboard now needs to evolve from a raw operational ledger into a product-grade decision operations surface.

Dashboard v2 must preserve the existing ledger view, but reposition it as the **Ledger**: the canonical decision history and audit surface. The new default landing experience should be **Project Pulse**, a summary and triage view that makes Bicameral feel like the project memory is awake before confusion becomes expensive.

The product must also support an expanding integrations roadmap: Linear, Jira, Notion, GitHub, Slack, Google Drive, local folders, and future providers. These integrations require a settings and configuration system that handles active/passive ingest modes, source scopes, credentials, audit trails, freshness, dead-letter queue visibility, privacy controls, and failure states.

Team mode currently flows through a Google Drive file-share substrate. That means the UI must not pretend to be a real-time team server. It must clearly distinguish:

1. Local knowledge.
2. Last confirmed shared team knowledge.
3. Potentially stale team awareness while disconnected from Google Drive.

The design target is a local-first, team-aware, Drive-synchronized product that remains useful offline while honestly marking shared state as stale when appropriate.

## 2. Product Principles

### 2.1 Local-first continuity

Bicameral must remain useful when disconnected. Local Project Pulse, Ledger, drift checks, local proposed decisions, diagnostics, and personal preferences should remain available without Google Drive.

### 2.2 Team-awareness without false certainty

When Google Drive is disconnected or stale, the UI must not imply shared team certainty. Team-affecting actions should either be queued locally or paused, depending on risk.

### 2.3 Two-chamber product model

Bicameral should visually and conceptually express its name.

- **Intent Chamber:** decisions, requirements, PRDs, meetings, Slack threads, Jira/Linear tickets, open questions, and ratification.
- **Execution Chamber:** code changes, commits, pull requests, agent actions, implementation evidence, and drift.
- **Bicameral Bridge:** MCP preflight, decision ledger, active/passive ingest, ratification, sync, and drift detection.

### 2.4 Ledger preservation

The current ledger-oriented view is valuable and must not be discarded. It should become the dedicated **Ledger** view.

### 2.5 Settings as product infrastructure

Settings are not a later polish pass. Incoming integrations make settings, scopes, credentials, team sync, auditability, themes, and accessibility foundational.

### 2.6 Accessibility by default

WCAG accessibility must be designed into the first version of Dashboard v2. It cannot be bolted on later without turning the UI into a haunted checkbox museum.

## 3. Primary Navigation Model

Dashboard v2 should use this core navigation structure.

| View | Purpose |
|---|---|
| Pulse | Default landing view for health, attention, freshness, recent learning, and suggested next action. |
| Ledger | Canonical decision record, retaining the current detailed grouped decision UI. |
| Ratification | Product-owner and decision-owner workflow for approving, rejecting, or superseding proposed decisions. |
| Drift | Implementation mismatch view with evidence, affected files, symbols, commits, and next actions. |
| Sources | Source ingest visibility by connector, source group, and capture channel. |
| Audit | Operational and security audit trail, including source ingest attempts, auth events, refusals, and DLQ summaries. |
| Integrations | Connection and configuration center for Linear, Jira, Notion, GitHub, Slack, Google Drive, and future sources. |
| Settings | Profile, team sync, themes, accessibility, privacy, diagnostics, and advanced controls. |

A compact sidebar is recommended for desktop. Mobile/tablet can collapse into a top-level menu with persistent sync status.

## 4. Information Architecture

### 4.1 Pulse

Project Pulse is the landing experience. It answers:

- Is project memory current?
- What needs attention?
- What did Bicameral recently learn?
- Where is drift or implementation risk?
- What should the user do next?
- Is team awareness fresh, stale, offline, or conflicted?

Recommended layout:

1. Dashboard header.
2. Team Sync status and freshness banner if needed.
3. Two-Chamber Overview.
4. Project Pulse summary.
5. Needs Attention queue.
6. Recently Learned queue.
7. Suggested Next Move.
8. Short activity timeline.
9. Links into Ledger, Ratification, Drift, Sources, and Audit.

### 4.2 Ledger

The Ledger is the canonical decision record. It retains the existing ledger view and makes it intentional.

Ledger answers:

- What decisions exist?
- What source did they come from?
- What is their status?
- Which decisions are ratified, proposed, rejected, superseded, reflected, or drifted?
- Which feature/source group do they belong to?
- What code references or commits are linked?

Recommended Ledger subtitle:

> Complete decision history, source groups, ratification state, implementation linkage, and drift indicators.

Ledger must be available while offline or stale, with a visible freshness warning:

> Ledger is showing local state plus the last confirmed team sync. Team updates may be missing until Google Drive reconnects.

### 4.3 Ratification

Ratification is the decision workflow surface. It must support PM-accessible approval and reject/supersede actions without requiring users to live inside Claude Code.

Ratification items should show:

- Decision statement.
- Source evidence.
- Source type and source reference.
- Feature area.
- Risk level.
- Related code references.
- Related decisions and supersession candidates.
- Signer/actor metadata.
- Current team freshness state.
- Allowed actions.

Actions:

- Ratify.
- Reject.
- Request changes.
- Supersede.
- Open full context.
- Copy source reference.

### 4.4 Drift

Drift is the implementation mismatch view.

Drift items should show:

- Original decision.
- Expected behavior.
- Observed implementation behavior.
- Evidence location.
- File, symbol, line range, commit, or PR.
- Severity or level.
- Suggested next action.
- Whether the team ledger is current.

Actions:

- Open evidence.
- Mark reviewed.
- Create follow-up proposal.
- Supersede decision.
- Re-run drift check.

### 4.5 Sources

Sources provide ingest visibility and source-group history.

Source categories:

- Issue Trackers: Linear, Jira.
- Documentation: Notion, Google Drive.
- Communication: Slack.
- Source Control: GitHub.
- Local and Advanced: Local folder, transcript directory, manual paste.

Each source group should show:

- Source name.
- Status.
- Last checked.
- Last accepted ingest.
- Last refused ingest.
- Active/passive mode.
- Scope summary.
- Decisions created.
- Pending proposals.
- Drifted decisions.
- Audit link.

### 4.6 Audit

Audit is not the same as Ledger.

Ledger is the product decision record. Audit is the operational/security event trail.

Audit should include:

- Source ingest attempts.
- Source ingest accepted/refused.
- Source auth granted/revoked.
- Soft-gate WARN + DLQ events.
- Hard-gate security refusals.
- Team sync events.
- Credential changes.
- Configuration changes.
- Ratification actions.
- Reset/replay events.

Audit must be filterable by source, event type, severity, actor, time range, and disposition.

## 5. Dashboard Header

The header must expose product identity, project context, and freshness.

Required elements:

- Bicameral wordmark/logo.
- Current repository or project name.
- Mode indicator: Solo or Team.
- Team Sync pill.
- Source freshness summary.
- Active user identity, if available.
- Settings entry point.
- Theme-aware visual design.

Example header states:

- `Team Sync: Current`
- `Team Sync: Stale`
- `Team Sync: Offline`
- `Team Sync: Auth Needed`
- `Team Sync: Conflict`

Clicking the Team Sync pill should open Team Sync detail.

## 6. Team Sync and Google Drive Freshness

### 6.1 Current team substrate

All team functions currently flow through Google Drive file share. The UI must treat Drive as the shared event substrate, not as a real-time server.

### 6.2 Team sync states

```ts
type TeamSyncState =
  | "solo"
  | "team_connected"
  | "team_syncing"
  | "team_stale"
  | "team_offline"
  | "team_auth_expired"
  | "team_conflict"
  | "team_read_only_fallback";
```

User-facing copy:

| State | Copy |
|---|---|
| team_connected | Team memory is current. |
| team_syncing | Checking Google Drive for team updates. |
| team_stale | Local memory is usable, but team updates have not been confirmed since the last sync. |
| team_offline | Google Drive is unavailable. Bicameral is using local memory until reconnect. |
| team_auth_expired | Google Drive access needs to be refreshed before team updates can sync. |
| team_conflict | Two or more team events need reconciliation. |
| team_read_only_fallback | Team state is stale. Review is available, but team-affecting actions are paused. |

### 6.3 Freshness Boundary

Introduce **Freshness Boundary** as a visible concept.

Recommended copy:

> Bicameral can keep working locally, but it must not imply team certainty until Google Drive sync is current.

Freshness card fields:

- Team backend: Google Drive.
- Last confirmed sync.
- Local queued events.
- Remote freshness state.
- Last error.
- Manual sync button.
- Reconnect Google Drive button.

### 6.4 Offline and stale behavior

Safe while offline or stale:

- View local Pulse.
- View Ledger.
- View previously synced decisions.
- Run local drift checks.
- View local audit.
- Change personal preferences.
- Draft proposed decisions locally.
- Run preflight against local state with stale warning.

Allowed offline but queued:

- Create local proposed decisions.
- Add local notes.
- Capture local ingest.
- Create pending ratification proposals.
- Create local audit entries.

Paused while stale/offline:

- Final shared ratification.
- Reject or supersede shared decisions.
- Team integration scope changes.
- Shared credential changes.
- Team access changes.
- Reset/replay shared ledger state.
- Conflict resolution.

## 7. Integrations and Sources

### 7.1 Integration Center

Integrations must be managed through a unified configuration center.

Each integration card should include:

- Name.
- Category.
- Connection status.
- Active/passive mode.
- Scope summary.
- Credential status.
- Last checked.
- Last accepted ingest.
- Last error.
- Configure action.
- Test connection action.
- Audit action.
- Revoke or disconnect action where allowed.

### 7.2 Required source categories

```text
Sources
  Issue Trackers
    Linear
    Jira

  Documentation
    Notion
    Google Drive

  Communication
    Slack

  Source Control
    GitHub

  Local / Advanced
    Local Folder
    Transcript Directory
    Manual Paste
```

### 7.3 Source freshness vs team freshness

Source freshness and team ledger freshness are different.

Example:

- Jira source freshness: `connected, last checked 2 minutes ago`.
- Team ledger freshness: `stale, Google Drive last confirmed 1 hour ago`.

The UI must not collapse these states.

```ts
type SourceFreshness = {
  sourceId: string;
  status: "connected" | "syncing" | "stale" | "offline" | "auth_expired" | "misconfigured";
  lastCheckedAt?: string;
  lastIngestedAt?: string;
  lastError?: string;
};

type TeamLedgerFreshness = {
  backend: "google_drive";
  status: "current" | "syncing" | "stale" | "offline" | "auth_expired" | "conflict";
  lastConfirmedAt?: string;
  queuedLocalEvents: number;
};
```

### 7.4 Jira design requirements

Jira is now a first-class source.

Jira supports:

- Active ingest by issue URL or key.
- Passive ingest by project, issue type, status transition, label, component, or JQL.
- Comment capture.
- Transition capture.
- Linked PR capture.
- Optional ratification command surface.

Jira integration settings:

```text
Connection
  Jira site URL
  Auth method
  Connected account
  Test connection
  Revoke credentials

Scope
  Projects
  Issue types
  Components
  Labels
  Status transitions
  Advanced JQL

Ingest Behavior
  Active ingest
  Passive ingest
  Comment capture
  Transition capture
  Linked PR capture

Ratification
  Enable @bicameral commands
  Allowed actions: ratify, reject, request changes, supersede
  Identity mapping required
  Unknown actor behavior: capture as proposal, do not finalize

Audit
  Last pull
  Last accepted ingest
  Last refused ingest
  Last auth event
  View source audit
```

JQL should be hidden under Advanced. Basic project/label/status controls should be the default.

### 7.5 Linear and Jira shared pattern

Do not design separate one-off UIs for Linear and Jira. Design an **Issue Tracker Source** pattern and instantiate both.

Shared fields:

- Workspace/site.
- Project/team.
- Issue type.
- Labels/components.
- Status transitions.
- Comment capture.
- Linked implementation references.
- Ratification command configuration.
- Active/passive mode.

## 8. Settings

### 8.1 Settings sections

Settings should include:

1. Profile and Preferences.
2. Workspace / Project.
3. Team Sync.
4. Integrations.
5. Source Scopes.
6. Ratification Surfaces.
7. Automation and Hooks.
8. Privacy and Telemetry.
9. Themes.
10. Accessibility.
11. Diagnostics.
12. Advanced / Danger Zone.

### 8.2 Profile and preferences

Personal preferences are local and always editable.

Required fields:

- Display name.
- Email, optional unless team identity requires it.
- Timezone: system or fixed timezone.
- Locale/language.
- Date/time format.
- First day of week.
- Theme.
- Density.
- Font scale.
- Reduced motion.
- High contrast.

Timezone must be implemented early because audit, ratification, source ingest, and Drive sync are time-sensitive. Store audit timestamps in UTC. Display them using the user's selected timezone.

### 8.3 Team preferences

Team preferences are Drive-backed and sync-sensitive.

Examples:

- Shared source scopes.
- Shared integration modes.
- Shared passive ingest settings.
- Team folder config.
- Role/capability mapping.
- Shared audit retention policy.
- DLQ retention defaults.
- Shared rate/size limits.

Team preferences should be editable only when Drive is connected and current.

## 9. Identity, Roles, and Capabilities

### 9.1 No full login for solo mode

Solo mode should not require a Bicameral login. The trust boundary is the local OS user account.

### 9.2 Team mode identity

Team mode identity should come from Google Drive OAuth or explicit team config. The UI should show the current identity, but it should not imply Bicameral-hosted account authority.

### 9.3 Hosted/shared future

If hosted/shared deployment exists later, a real auth shim is required. The UI should be designed around identity context and capabilities so this future path does not require a rewrite.

### 9.4 Roles

Recommended roles:

- Local Owner.
- Viewer.
- Reviewer.
- Decision Owner.
- Integrator.
- Admin.

### 9.5 Capabilities

Use capabilities under the hood. The UI should check capabilities and the backend must enforce them.

```ts
type Capability =
  | "local.read"
  | "local.write"
  | "team.read"
  | "team.propose"
  | "team.ratify"
  | "team.reject"
  | "team.supersede"
  | "team.configure_integrations"
  | "team.manage_access"
  | "team.danger.reset"
  | "credential.write"
  | "credential.revoke"
  | "audit.read"
  | "audit.security.read"
  | "diagnostics.read";
```

Capabilities should be conditional on team freshness.

Example:

> Ratification paused until team memory is current.

This is different from:

> You do not have permission.

The UI must distinguish temporary freshness constraints from actual permission denial.

## 10. Themes

### 10.1 Minimum themes

Required:

- System.
- Light.
- Dark.
- High contrast light.
- High contrast dark.

Optional branded themes:

- Ledger Parchment.
- Deep Navy.
- Terminal Minimal.

### 10.2 Theme controls

- Theme mode.
- Density: comfortable / compact.
- Font scale: default / large / extra large.
- Reduced motion.
- Monochrome status indicators.

### 10.3 Theme persistence

Theme and accessibility preferences should persist locally and must not depend on Google Drive.

## 11. Accessibility Requirements

Target WCAG 2.2 AA.

Requirements:

- Status must not rely on color alone.
- All status chips require visible text labels.
- Keyboard access for all controls.
- Clear focus states.
- Dialogs/drawers must trap and restore focus.
- Forms need explicit labels.
- Errors must be text-described.
- Tables/lists must preserve semantic structure.
- UI must remain usable at 125%, 150%, and 200% zoom.
- Reduced motion must disable nonessential animation.
- High contrast themes must meet AA and aim for AAA where practical.

## 12. Component Architecture

Suggested component decomposition:

```text
DashboardApp
  AppShell
    SidebarNav
    DashboardHeader
    TeamSyncStatusPill
    SourceFreshnessSummary
  PulseView
    ChamberOverview
    ProjectPulseCard
    NeedsAttentionQueue
    RecentlyLearnedQueue
    SuggestedNextMoveCard
    ActivityTimeline
  LedgerView
    LedgerFilters
    LedgerGroupList
    LedgerDecisionRow
    LedgerDecisionDetailDrawer
  RatificationView
    RatificationQueue
    RatificationDecisionCard
    RatificationDetailDrawer
  DriftView
    DriftSummary
    DriftEvidenceCard
    DriftDetailDrawer
  SourcesView
    SourceCategoryGroup
    SourceStatusCard
    SourceDetailDrawer
  AuditView
    AuditFilters
    AuditEventTable
    DlqSummaryPanel
  IntegrationsView
    IntegrationCategoryGroup
    IntegrationCard
    IntegrationDetailDrawer
    ConnectionTestResult
  SettingsView
    ProfileSettingsPanel
    WorkspaceSettingsPanel
    TeamSyncSettingsPanel
    ThemeSettingsPanel
    AccessibilitySettingsPanel
    PrivacyTelemetryPanel
    DiagnosticsPanel
    AdvancedDangerZone
```

## 13. Data Contracts

### 13.1 Identity context

```ts
type BicameralMode = "solo" | "team" | "hosted";

type UserIdentity = {
  id: string;
  displayName?: string;
  email?: string;
  source: "local_os" | "config" | "google_drive" | "github" | "linear" | "jira" | "hosted_auth";
  timezone: "system" | string;
  locale: string;
};

type IdentityContext = {
  mode: BicameralMode;
  user: UserIdentity;
  roles: string[];
  capabilities: Capability[];
  trustBoundary: "os_user" | "provider_oauth" | "team_server_auth" | "hosted_session";
};
```

### 13.2 Integration config summary

```ts
type IntegrationStatus =
  | "connected"
  | "not_connected"
  | "misconfigured"
  | "pending"
  | "coming_soon"
  | "auth_expired";

type IntegrationConfigSummary = {
  id: string;
  name: string;
  category: string;
  status: IntegrationStatus;
  description: string;
  localFirst: boolean;
  requiresCredentials: boolean;
  supportsActiveIngest: boolean;
  supportsPassiveIngest: boolean;
  supportsRatificationSurface: boolean;
  lastCheckAt?: string;
  lastIngestedAt?: string;
  lastError?: string;
};
```

### 13.3 Accessibility preferences

```ts
type DensityMode = "comfortable" | "compact";

type AccessibilityPreferences = {
  reducedMotion: boolean;
  highContrast: boolean;
  fontScale: "default" | "large" | "extra_large";
  density: DensityMode;
  strongFocusRing: boolean;
  monochromeStatusIndicators: boolean;
};
```

## 14. Implementation Milestones

### Milestone 1: Information architecture and navigation

- Add navigation model.
- Rename current dashboard/history table view to Ledger.
- Preserve existing Ledger functionality.
- Add Pulse as default landing view.
- Add Team Sync status pill.

### Milestone 2: Pulse visual redesign

- Add two-chamber overview.
- Improve Project Pulse hierarchy.
- Add stale/offline freshness warnings.
- Add activity timeline and next action cards.

### Milestone 3: Ratification and Drift surfaces

- Build Ratification view.
- Build Drift view.
- Add decision detail drawers.
- Add capability and freshness-based disabled states.

### Milestone 4: Integrations and Sources

- Build Integration Center.
- Add source categories.
- Add Linear and Jira as Issue Tracker sources.
- Add Notion, GitHub, Slack, Google Drive, local folder, and transcript directory placeholders or active configs as appropriate.
- Add source freshness separation from team freshness.

### Milestone 5: Settings, themes, and accessibility

- Add Settings shell.
- Add profile/preferences.
- Add light/dark/system/high-contrast themes.
- Add timezone/locale scaffolding.
- Add accessibility preferences.
- Persist preferences locally.

### Milestone 6: Audit and diagnostics

- Add Audit view.
- Add DLQ summary visibility.
- Add diagnostics view.
- Add smoke test status, MCP registration status, hook status, sync status, and config parse status.

### Milestone 7: WCAG and visual QA

- Keyboard pass.
- Screen reader label pass.
- Focus management pass.
- Contrast audit.
- Zoom testing.
- Reduced motion verification.
- Stale/offline state review.

## 15. Acceptance Criteria

Dashboard v2 is complete when:

1. Project Pulse is the default landing view.
2. The existing detailed decision UI remains available as Ledger.
3. Ledger clearly describes itself as the canonical decision record.
4. Team Sync state is globally visible.
5. Stale/offline Drive state clearly distinguishes local continuity from shared team certainty.
6. Ratification has a dedicated workflow surface.
7. Drift has a dedicated evidence surface.
8. Sources and integrations are separated but connected.
9. Jira is represented as a first-class source alongside Linear.
10. Integration settings support active/passive modes, scopes, auth state, test connection, revoke, and audit.
11. Source freshness and team ledger freshness are not collapsed.
12. Personal preferences include theme, timezone, locale/language scaffold, density, font scale, and accessibility controls.
13. Light, dark, system, high-contrast light, and high-contrast dark themes exist.
14. WCAG 2.2 AA requirements are tested and documented.
15. Role/capability and freshness constraints are represented in the UI.
16. No team-affecting action silently succeeds while Drive state is stale or offline.
17. Local-first behavior remains useful when disconnected.

## 16. Non-Goals

- Full Bicameral-hosted login for solo mode.
- Enterprise RBAC in the first implementation slice.
- Replacing the existing Ledger view.
- Building a real-time team server UI before backend support exists.
- Treating Google Drive sync as equivalent to live shared state.
- Requiring cloud services for local dashboard use.

## 17. Final Design Thesis

Bicameral Dashboard v2 should feel like a calm project memory system, not a raw event table. It should show what the team decided, what the code now reflects, what needs human judgment, and whether shared team memory is current.

The UI should preserve Bicameral's local-first posture while giving teams enough structure to trust it during real work.
