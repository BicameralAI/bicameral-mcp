# bicameral-dashboard

Launch the live decision dashboard — a local browser tab. The Dashboard v2 shell opens to a Project Pulse landing view (health, what needs attention, recently learned, suggested next move) and carries a sidebar to the Ledger — the canonical decision record, every tracked decision grouped by feature area — which pushes real-time updates whenever `bicameral.ingest` or `bicameral.link_commit` writes new data. Further surfaces (Ratification, Drift, Sources, Audit, Integrations, Settings) are being built out across the Dashboard v2 milestones.

## Triggers

Fire this skill when the user says any of:
- "open dashboard"
- "show live history"
- "launch dashboard"
- "open the decision dashboard"
- "show the live view"
- "open the ledger in the browser"

Do NOT fire on preflight, ingest, drift, or search prompts — those have dedicated skills.

## Steps

1. Call `bicameral.dashboard` (no required arguments).

2. Render the dashboard URL:

   ```
   Dashboard: {url}  ({status})
   ```

   If `status == "started"`: tell the user the server just started and prompt them to open the URL.
   If `status == "already_running"`: confirm the existing URL.

4. If `open_browser` was true (the default), say:

   > Open **{url}** in your browser. The page updates live as decisions are ingested or commits are synced.

5. Do not call any other bicameral tools in this flow. The dashboard serves history independently.

## Notes

- The server runs as a background task inside the MCP process and persists for the session.
- Port is saved to `~/.bicameral/dashboard.port` for reference.
- The HTML page auto-reconnects if the SSE stream is interrupted (e.g., sleep/wake).
- The dashboard UI is built with Vite + TypeScript + Preact (ADR-0005); source lives in `dashboard/frontend/` and the build emits the single-file bundle `assets/dashboard.html`. Regenerate with `cd dashboard/frontend && npm ci && npm run build`.
- The pre-v2 dashboard is preserved at `assets/dashboard-legacy.html`, served at the `/legacy` route; the v2 Ledger view embeds it until the native Ledger component port lands.
- Decision rows with `status === 'pending'` carry a tooltip nudging the user to run `/bicameral-sync` in their Claude Code session. The dashboard does not trigger compliance resolution itself — it surfaces the pending state and points at the skill that resolves it.
