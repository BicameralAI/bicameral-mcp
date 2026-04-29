# Training

Long-form, multi-step walkthroughs that teach a *concept*, not a tool. Use
training docs when a feature introduces an idea the user must internalise
before the reference docs make sense.

Examples of concepts that warrant training:

- *"What does `pending` vs `reflected` vs `drifted` vs `ungrounded` actually
  mean, and how does the ledger derive each?"*
- *"What's a content-hash CAS guard, why does the server reject your verdict
  when it doesn't match, and how do you recover?"*
- *"How does the continuity matcher decide a renamed function is the same
  identity?"*

If the answer fits in a guide's intro paragraph, it's a guide, not a training
doc.

See [`docs/DEV_CYCLE.md` §8](../DEV_CYCLE.md#8-documentation-requirements-per-release)
for when training is required by the release process (rule of thumb: only when
the feature introduces a concept, not just a tool).

## Index

| Topic | Status |
|---|---|
| [Cosmetic vs semantic drift](./cosmetic-vs-semantic.md) | Active |

## Template

```markdown
# <Concept> — Training

## Why this exists
Two sentences. The mental-model gap this doc closes.

## Prerequisites
What the reader should already understand or have read.

## The concept
The actual teaching content. Use diagrams, worked examples, anti-examples.
Be willing to spend 1000+ words if the concept is load-bearing.

## Worked example
End-to-end scenario tying the concept to a real tool call.

## Common pitfalls
Numbered list of mistakes people make and the corrected behaviour.

## See also
Links to relevant guides, demos, and source files.
```

## Authoring rules

- Training docs are not release-blocking unless `DEV_CYCLE.md` §8 says so for
  the specific feature class.
- One concept per file. If you find yourself splitting into Part 1 / Part 2,
  the concept is probably two concepts.
- Reviewers may push back on training that overlaps with an existing guide —
  guides are the canonical reference; training is supplementary.
