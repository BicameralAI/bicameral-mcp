# bicameral-dashboard

Launch the live decision dashboard — a local browser tab that shows every tracked decision grouped by feature area and pushes real-time updates whenever `bicameral.ingest` or `bicameral.link_commit` writes new data.

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

2. Render the response:

   ```
   Dashboard: {url}  ({status})
   ```

   If `status == "started"`: tell the user the server just started and prompt them to open the URL.
   If `status == "already_running"`: confirm the existing URL.

3. If `open_browser` was true (the default), say:

   > Open **{url}** in your browser. The page updates live as decisions are ingested or commits are synced.

4. Do not call any other bicameral tools in this flow. The dashboard serves history independently.

## Notes

- The server runs as a background task inside the MCP process and persists for the session.
- Port is saved to `~/.bicameral/dashboard.port` for reference.
- The HTML page auto-reconnects if the SSE stream is interrupted (e.g., sleep/wake).
- To replace the placeholder UI with the full Svelte bundle, run `make dashboard` from the repo root after `pilot/demo2` is built.
