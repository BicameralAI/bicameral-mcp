I'm about to refactor `app/src/lib/git/reorder.ts` — pulling out the `reorder()` function entirely. We're moving away from drag-and-drop reordering; the new flow is going to be a text editor where the user types the desired commit order as a numbered list and we apply it from there. No more drag-drop interactions on this surface.

Help me start the refactor. I'll handle the call-site cleanup separately.
