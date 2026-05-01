# Demo: v0 user flow e2e (split-screen, two views)

**Audience**: first-time evaluators who want to see the loop without running it.
**Time**: ~6 min PM view, ~10 min Dev view.
**Prereqs**: none — videos play in any browser.

## What you'll see

A continuous Claude Code CLI session — recorded once, then split in post
into two persona-shaped videos:

- **Left pane** of the recording — `xterm` running `claude -p <composite-prompt>`
  with `bicameral-mcp` registered as the only MCP server. The LLM's reasoning,
  tool calls, and outputs render in real time via a small stream-json formatter.
- **Right pane** — `chromium` pointed at the bicameral dashboard sidecar
  (`http://localhost:<port>`). Live SSE updates as the session emits ledger
  writes. **Because both PM scenes and the Dev scene share one MCP process,
  the dashboard state in the post-implementation chapter literally reflects
  the commits the dev made on screen** — not a re-hydration from a separate
  ledger.

### `pm.mp4` (PM view)

| Chapter | Tools used | What's on screen |
|---|---|---|
| 1. Post-meeting | `bicameral.dashboard`, `bicameral.ingest`, `bicameral.ratify` | PM ingests three GitHub Desktop roadmap decisions; the dashboard fills with proposed-then-ratified entries. |
| _Transition slide_ | _(ffmpeg-generated)_ | "Dev now implements the change → Returning to PM after the implementation has landed." |
| 2. Post-implementation | `bicameral.history`, `bicameral.ratify` | PM calls `history`; the cherry-pick decision now shows `status=reflected` (was pending). PM ratifies the post-implementation state. |

### `dev.mp4` (Dev view)

| Step | Tool | What's on screen |
|---|---|---|
| 1 | `bicameral.preflight` | Surfaces the cherry-pick decision before any edit. |
| 2 | `Edit` | Single-line annotation added to `app/src/lib/git/cherry-pick.ts`. |
| 3 | `Bash` (`git add` + `git commit`) | Real commit on the desktop/desktop fixture. |
| 4 | `bicameral.link_commit` | Detects drift candidates against decisions bound to that file. |
| 5 | `bicameral.resolve_compliance` | Verdict per pending compliance check (compliant / drifted / not_relevant). |
| 6 | `bicameral.ingest` (source=agent_session) | Captures any session-end corrections. |

A third file, `full.mp4`, contains the full unbroken arc — useful if you
want to see the Dev's commits land in the dashboard without the
transition cut.

## How to access the latest demos

The MP4s are generated on demand and **not committed to git** — they live in
the `v0-user-flow-e2e-demos` artifact attached to the manual workflow run.

1. Open the [v0 user flow e2e workflow runs](../../../../actions/workflows/v0-user-flow-e2e.yml).
2. Filter to runs triggered via "Run workflow" with `record_demo = true`.
3. Scroll to the run's **Artifacts** section, download `v0-user-flow-e2e-demos`.
4. Unzip → `pm.mp4`, `dev.mp4`, `full.mp4`.

Artifact retention is 90 days. On a release cut (per
[`docs/DEV_CYCLE.md` §6.7](../DEV_CYCLE.md#67-github-release)), the maintainer
attaches the latest demos to the GitHub release for permanent URLs.

## How to record a fresh set

Demos are intentionally manual — not gated on every PR — because they cost
~25–35 minutes wall + Claude API spend per run.

1. Trigger via the workflow's **Run workflow** dropdown (UI), or:
   ```bash
   gh workflow run v0-user-flow-e2e.yml -f record_demo=true
   ```
2. Wait for the run to finish. The assertion step still runs first and is
   the authority on pass/fail; the recording step is `continue-on-error`,
   so a flake never blocks merge.
3. Download the `v0-user-flow-e2e-demos` artifact as above.

## How the split works

`tests/e2e/record_demo.sh` runs one continuous claude session driven by
`tests/e2e/prompts/composite-demo.md` (three scenes: PM-pre, Dev, PM-post).
The session's stream-json output is piped through
`tests/e2e/demo_renderer.py`, which:

1. Pretty-prints to stdout so the xterm shows readable text.
2. Watches the tool-call timeline and writes wall-clock timestamps to
   `composite-demo-scenes.txt` at two boundaries:
   - **Scene 1 → 2** = first `bicameral.preflight` call (Dev starts).
   - **Scene 2 → 3** = first `bicameral.history` call after any
     `bicameral.link_commit` (PM resumes).
3. Persists the raw stream-json transcript for forensic review.

After ffmpeg stops, the script trims `full.mp4` at those two timestamps
into `pm-pre`, `dev`, `pm-post`, generates a 4-second transition slide via
`drawtext`, and concats `pm-pre + transition + pm-post → pm.mp4`.

If scene markers are missing (e.g., the LLM declined a step), the script
falls back to keeping `full.mp4` only — the recording is preserved but
the split is skipped.

## Next

- [End-to-end suite README](../../tests/e2e/README.md) — the assertion-only
  path that runs on every qualifying PR.
- [`#108` spec](https://github.com/BicameralAI/bicameral/issues/108) — the
  six canonical flows the composite prompt orchestrates.
