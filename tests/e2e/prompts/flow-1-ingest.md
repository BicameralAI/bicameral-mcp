I just reviewed the GitHub Desktop roadmap and want to capture some of their recent feature decisions in bicameral so we can track them.

Here are three roadmap items:

1. **High signal notifications (2.9.10 and 3.0.0)** — Receive a notification when checks fail. Receive a notification when your pull request is reviewed.

2. **Improved commit history (2.9.0)** — Reorder commits via drag/drop. Squash commits via drag/drop. Amend last commit. Create a branch from a previous commit.

3. **Cherry-picking commits from one branch to another (2.7.1)** — Cherry-pick commits with a context menu and interactively.

Please ingest these as decisions into the bicameral ledger. The source is `desktop/desktop:docs/process/roadmap.md`.

Then bind the cherry-pick decision to `app/src/lib/git/cherry-pick.ts` — specifically the `CherryPickResult` enum near the top of that file (lines 31–60). That gives us a code anchor to validate against in later flows.

Finally, ratify all three decisions via `bicameral.ratify` so they move from `proposed` to `ratified` — the team has reviewed and adopted them. Briefly confirm what landed (decision IDs, signoff state, and which decision is bound where) so the rest of this session can build on a clean ratified ledger.
