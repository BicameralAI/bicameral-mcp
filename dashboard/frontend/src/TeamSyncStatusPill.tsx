// Team Sync status pill. Renders the honest current state — `Solo` — with
// a visible text label (not color-only) so the state is accessible. No
// backend call: no Drive team-sync endpoint is wired this milestone.

export function TeamSyncStatusPill() {
  return (
    <span class="sync-pill" role="status" aria-label="Team sync status: Solo">
      <span class="sync-pill-dot" aria-hidden="true" />
      <span class="sync-pill-label">Solo</span>
    </span>
  );
}
