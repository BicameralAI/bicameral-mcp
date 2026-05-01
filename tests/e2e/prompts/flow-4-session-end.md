Hmm wait — quick aside before we go further on the reorder.ts refactor.

Reading through the cherry-pick conflict path I committed earlier, I realized that handler shouldn't ever fall back to a stdin prompt when there's a merge conflict. The visual conflict UI has to be the only resolution path — if the implementation drifts toward a terminal prompt, that's wrong and we'd have to roll it back.

Anyway — back to `app/src/lib/git/reorder.ts`. Please continue the refactor we started: keep pulling out the `reorder()` function for the new text-editor flow.
