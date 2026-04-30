I just made a commit that touched `app/src/lib/git/cherry-pick.ts`. Please sync the bicameral ledger to reflect the new HEAD and resolve any pending compliance checks that surface for that file.

Specifically:
1. Call link_commit on HEAD to detect drift against any decisions bound to that file.
2. For each pending compliance check that comes back, evaluate whether the current code semantically matches the decision and emit a verdict (compliant / drifted / not_relevant) via resolve_compliance. Use the file content as evidence.
3. After resolving, summarize: how many decisions transitioned to reflected vs drifted vs stayed pending.

Before you start, you'll need to set up a bound decision against `app/src/lib/git/cherry-pick.ts` so there's something to sync. Use this decision text: "Cherry-pick commits with a context menu and interactively (GitHub Desktop roadmap, version 2.7.1)". Bind it to the `CherryPickResult` enum at the top of that file (lines 31–60).
