# Composite v0 user-flow demo (single session, three scenes)

This is a continuous demo session that will be split in post into a "PM
view" video (pm.mp4) and a "Dev view" video (dev.mp4). Walk through
the three scenes below in order. Do not skip steps. Do not abbreviate.

Before you begin: call `bicameral.dashboard` so the dashboard sidecar
binds and the right pane of the recording has live ledger updates to
show.

---

## SCENE 1 — Post-meeting (PM persona)

You are the PM. The team just reviewed the GitHub Desktop roadmap.
Ingest the following decisions into the ledger via `bicameral.ingest`:

1. **High signal notifications (versions 2.9.10 and 3.0.0)** — Receive
   a notification when checks fail. Receive a notification when your
   pull request is reviewed.
2. **Improved commit history (version 2.9.0)** — Reorder commits via
   drag/drop. Squash commits via drag/drop. Amend last commit. Create
   a branch from a previous commit.
3. **Cherry-picking commits from one branch to another (version 2.7.1)**
   — Cherry-pick commits with a context menu and interactively. Bind
   this decision to `app/src/lib/git/cherry-pick.ts` (specifically the
   `CherryPickResult` enum near the top of the file).

Source: `desktop/desktop:docs/process/roadmap.md`.

After `bicameral.ingest` returns, ratify the decisions you just
ingested via `bicameral.ratify`. Briefly confirm what landed (decision
IDs and signoff state) so the viewer understands the ledger now has
proposed-then-ratified entries.

---

## SCENE 2 — Implementation (Dev persona)

You are now the dev. Walk through the implementation arc end-to-end:

1. Call `bicameral.preflight` on `app/src/lib/git/cherry-pick.ts` to
   surface relevant decisions before editing. Read the response — it
   should remind you about the cherry-pick decision from Scene 1.

2. Use the `Edit` tool to add a single-line comment near the top of
   `app/src/lib/git/cherry-pick.ts` referencing the cherry-pick
   roadmap decision (e.g.,
   `// Cherry-pick: roadmap v2.7.1 — context menu + interactive`).
   Keep it minimal and non-disruptive.

3. Stage and commit the change with `Bash`:
   - `git add app/src/lib/git/cherry-pick.ts`
   - `git commit -m "demo: annotate CherryPickResult with roadmap decision"`

4. Call `bicameral.link_commit` on `HEAD` to detect drift against any
   decisions bound to that file.

5. For each pending compliance check that `link_commit` surfaces, call
   `bicameral.resolve_compliance` with a verdict
   (compliant / drifted / not_relevant). Use the file's content as
   evidence.

6. If any non-trivial decisions emerged mid-session (corrections,
   constraint clarifications), capture them with `bicameral.ingest`
   using `source=agent_session`.

---

## SCENE 3 — Post-implementation (PM persona)

You are the PM again. The dev just landed their changes. Show how
the ledger evolved:

1. Call `bicameral.history`. The cherry-pick decision should now show
   `status=reflected` (or `compliant`) where it was previously
   pending or ungrounded.

2. Render a brief markdown table grouped by feature area, showing each
   decision's two axes — code-compliance status and human signoff
   state — so the viewer can scan it.

3. Ratify the post-implementation state of the cherry-pick decision
   via `bicameral.ratify` to acknowledge that what shipped matches
   what was decided.
