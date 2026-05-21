# ADR-0002: Google Drive-Backed Team Sync and Freshness Boundaries

**Status:** Accepted for planning  
**Date:** 2026-05-21  
**Related:** `docs/design/dashboard-v2-comprehensive-design.md`

## Context

Current Bicameral team functions flow through a Google Drive file-share substrate. Team mode replicates shared decision events through files that live in a user-provisioned Drive folder. This is local-first and simple, but it is not the same as a real-time team server.

The UI must therefore avoid implying that team state is always current. If Google Drive is disconnected, stale, or auth-expired, Bicameral can still operate locally, but shared team awareness may be incomplete.

## Decision

Model team sync as eventual consistency with explicit freshness states.

Required states:

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

The UI must distinguish:

1. Local state.
2. Last confirmed team state from Google Drive.
3. Potentially unknown remote changes while stale/offline.

The UI must also distinguish **source freshness** from **team ledger freshness**. Example: Jira may be connected and recently checked while Google Drive team sync is stale.

## Rationale

Without freshness boundaries, users may believe ratification or integration changes are operating on current shared state when they are not. That creates ghost decisions, stale ratification, conflict risk, and audit ambiguity.

Drive-backed sync should be presented honestly:

> Local-first, team-aware, Drive-synchronized.

## Action Policy

Safe while stale/offline:

- View Pulse from local state.
- View Ledger.
- View previously synced decisions.
- Run local drift checks.
- View local audit.
- Edit personal preferences.
- Draft local proposed decisions.

Allowed but queued:

- Create local proposed decisions.
- Capture local ingest.
- Add local notes.
- Create local audit events.

Paused while stale/offline:

- Final shared ratification.
- Reject or supersede shared decisions.
- Team integration scope changes.
- Shared credential changes.
- Team access changes.
- Shared reset/replay.
- Conflict resolution.

## UI Requirements

- Global Team Sync status pill.
- Freshness Boundary card when stale/offline.
- Action-level disabled reasons.
- Local queue count.
- Last confirmed sync timestamp.
- Manual sync action.
- Reconnect Google Drive action.
- Clear distinction between permission denial and freshness pause.

Example copy:

> Ratification paused until team memory is current.

Do not use:

> You do not have permission.

unless the user truly lacks the role/capability.

## Consequences

Positive:

- Preserves local-first usefulness.
- Avoids false team certainty.
- Makes Drive-backed eventual consistency understandable.
- Supports future team-server or hosted deployment without rewriting the UI model.

Tradeoffs:

- Adds state complexity.
- Requires capability checks to consider both role and freshness.
- Requires conflict and queued-event visibility.

## Acceptance Criteria

- Team Sync state is globally visible.
- Ledger and Pulse remain usable when Drive is stale/offline.
- Shared ratification and team configuration actions are paused or queued appropriately.
- Source freshness and team freshness are displayed separately.
- Stale/offline states are text-described, not only color-coded.
