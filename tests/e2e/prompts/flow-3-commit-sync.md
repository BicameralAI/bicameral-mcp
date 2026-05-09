Edit `app/src/lib/git/cherry-pick.ts` to add a one-line comment immediately above the `if (commits.length === 0)` early-return inside the `cherryPick` function: `// Empty cherry-pick set is a no-op; bail before any git invocation.`

Then run `git add app/src/lib/git/cherry-pick.ts && git commit -m "docs: annotate cherry-pick early-return"` to commit the change.
