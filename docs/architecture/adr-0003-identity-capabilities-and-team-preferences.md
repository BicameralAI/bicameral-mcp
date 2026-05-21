# ADR-0003: Identity, Capabilities, Preferences, and Access Gating

**Status:** Accepted for planning  
**Date:** 2026-05-21  
**Related:** `docs/design/dashboard-v2-comprehensive-design.md`

## Context

Bicameral is local-first. Solo mode should not require a hosted login because the operating-system user boundary is the local trust boundary. Team mode currently uses Google Drive as the shared substrate, which means identity may come from Google OAuth or team config rather than a Bicameral-hosted account system.

However, incoming team workflows require different visibility and action rights. Ratification, integration configuration, credential revocation, team settings, audit access, and destructive operations cannot all be available to every user once shared team workflows exist.

## Decision

Design the UI around an `IdentityContext` and capability-based access model.

Solo mode:

- No Bicameral login required.
- User is treated as `local_owner`.
- Trust boundary is `os_user`.

Team mode:

- User identity comes from Google Drive OAuth or team config.
- Roles/capabilities may be stored in shared team metadata.
- Capabilities are affected by team freshness state.

Future hosted/shared deployment:

- Requires real auth shim before privileged shared operations.
- UI model should already support hosted/session identity.

## Roles

Initial roles:

- `local_owner`
- `viewer`
- `reviewer`
- `decision_owner`
- `integrator`
- `admin`

## Capabilities

Use capabilities for UI gating and backend enforcement.

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

## Identity Context

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

## Preference Classes

Personal preferences are local and always editable:

- Theme.
- High contrast.
- Reduced motion.
- Density.
- Font scale.
- Timezone.
- Locale/language.
- Date/time format.
- First day of week.

Team preferences are shared and Drive-sync-sensitive:

- Source scopes.
- Integration modes.
- Passive ingest settings.
- Team folder configuration.
- Role/capability map.
- Audit retention defaults.
- DLQ retention defaults.
- Shared rate/size limits.

## Rationale

A full hosted login is unnecessary for solo mode and would undermine the local-first posture. But role/capability modeling is needed now so team workflows do not require a later UI rewrite.

Capability-based design avoids hard-coding everything to role names. It also lets the UI distinguish these cases:

- User lacks permission.
- User has permission, but Drive sync is stale.
- User has permission, but credentials are expired.
- User has permission, but action is unavailable in solo mode.

## Consequences

Positive:

- Keeps solo mode simple.
- Supports Drive-backed team workflows.
- Prepares for hosted/shared deployment.
- Enables clear action-level explanations.

Tradeoffs:

- Requires backend enforcement, not just UI hiding.
- Requires shared metadata for team role/capability mapping.
- Requires careful UX copy to distinguish permission denial from freshness pause.

## Acceptance Criteria

- Solo mode does not require login.
- Team mode can show current identity from Google Drive or config.
- UI gates actions by capability and freshness state.
- Permission denial and stale-state pause are visually and textually distinct.
- Personal preferences remain local and editable offline.
- Team preferences are editable only when team sync is current and capability permits.
