Just got out of our roadmap review for GitHub Desktop. Three items the team agreed to start tracking:

1. **High-signal notifications** (versions 2.9.10 and 3.0.0) — notify on failed checks, notify when a PR gets reviewed.
2. **Improved commit history** (2.9.0) — drag-and-drop to reorder commits, drag-and-drop to squash, amend last commit, branch from a previous commit.
3. **Cherry-picking commits between branches** (2.7.1) — context-menu cherry-pick and an interactive variant.

Source is `desktop/desktop:docs/process/roadmap.md`.

Two of these have an obvious code home so we can keep code in sync with intent later. The reorder/improved-commit-history piece anchors to `app/src/lib/git/reorder.ts` (the `reorder` function near the top of the file). The cherry-pick item anchors to `app/src/lib/git/cherry-pick.ts`, specifically the `CherryPickResult` enum (lines 31–60). Anchor those two so the ledger has something to verify against once we start changing the code.

I've already reviewed all three with the team and we're aligned — please sign these off on our end so we can move forward on a clean slate.
