# Demos

Runnable, ≤ 5-minute walkthroughs of headline functionality. Each demo takes a
viewer from "I don't know what this does" to "I see the value" without leaving
the file.

See [`docs/DEV_CYCLE.md` §12](../DEV_CYCLE.md#12-demo-scripts) for the
authoring rules and the demo template.

## Index

| # | Title | Audience | Status |
|---|---|---|---|
| 01 | First decision bind, search, drift detect | "what's the loop?" | planned |
| 02 | Commit-sync hook → resolve_compliance | "how does it play with git?" | planned |
| 03 | Continuity matcher: function rename auto-redirect (Phase 3) | "what about refactors?" | planned |
| 04 | Cosmetic-vs-semantic drift classifier (Phase 4) | "why no whitespace false-flags?" | planned |
| —  | [v0 user flow e2e (split-screen)](./v0-userflow-e2e.md) | "what does the loop look like end-to-end?" | live (manual workflow) |

## Authoring rules (summary)

- Run the demo end-to-end on a fresh clone before committing it.
- If the demo depends on a feature flag (e.g.
  `BICAMERAL_CODEGENOME_ENHANCE_DRIFT`), say so in **Prereqs**.
- Recordings (≤ 30 MB) live in `recordings/` next to the script.
- Update the demo whenever the underlying tool's response shape changes —
  enforced by the skill rule in `DEV_CYCLE.md` §9.

## Template

```markdown
# Demo NN: <Title>

**Audience**: <e.g. "first-time evaluator">
**Time**: <≤ 5 min>
**Prereqs**: <repo cloned, deps installed, MCP server running>

## What you'll see
1-paragraph spoiler.

## Setup
Copy-pasteable shell block.

## Walkthrough
Numbered steps, each with the exact tool call / command and the expected
output (truncated where it makes sense).

## What just happened
Plain-English read of the result. Tie it back to the user-value claim.

## Next
Pointer to the user guide and related demos.
```
