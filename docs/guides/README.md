# User Guides

Reference-style documentation for individual features. Pairs with the demos in
`docs/demos/` (which show *how it feels*) by answering *what it does, when to
use it, and what every field means*.

See [`docs/DEV_CYCLE.md` §8](../DEV_CYCLE.md#8-documentation-requirements-per-release)
for when a guide is required by the release process.

## Index

| Topic | Surface | Status |
|---|---|---|
| (none yet) | — | — |

## Template

```markdown
# <Feature> — User Guide

## What it does
One paragraph.

## When you'd use it
Bulleted scenarios.

## Quickstart
Smallest end-to-end example.

## Reference
Tool name, request shape, response shape, error modes.

## See also
Links to related guides + demo script.
```

## Authoring rules

- One guide per feature, named `<feature-slug>.md`.
- Guides are reference, not tutorial — show field shapes and error modes
  exhaustively. Tutorial-style content belongs in `docs/training/`.
- A guide referenced by a release PR's documentation checklist must exist by
  the time the release PR opens, not later.
- When a tool's response shape changes, update the matching guide in the same
  commit (per `DEV_CYCLE.md` §9 skill rule).
