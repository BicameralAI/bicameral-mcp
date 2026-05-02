---
name: qor-docs-technical-writing
description: Concise professional technical writing with narrative context around product conception, development, and life beyond. Use when drafting or editing READMEs, release notes, product briefs, architecture overviews, extension listings, or documentation where accuracy, technical rigor, and brevity are required.
phase: memory
tone_aware: false
gate_reads: ""
gate_writes: ""
---
# Technical Writing Narrative

## Overview

Produce succinct technical documents with verified claims and a clear narrative arc (conception, development, life beyond).

## Workflow

1. Confirm audience, purpose, medium, and source of truth (repo, specs, tickets).
2. List claims and map each to a source. Remove or qualify anything unverified.
3. Build a tight outline: problem, concept, implementation, current capabilities, future.
4. Write in short, high-signal sentences. Prefer verbs over adjectives.
5. Validate names, commands, settings, and paths against code or docs.
6. Trim for brevity: remove filler, consolidate, tighten.

## Source Precedence

When sources conflict, use this order:

1. Runtime behavior or tests (if reproducible in current repo state)
2. Source code in the current branch
3. Versioned governance artifacts (roadmap, backlog, changelog, release notes)
4. Tickets/discussion notes

If conflicts remain unresolved, mark claim status as `unknown` and document conflict.

## Claim-to-Source Mapping

For docs with consequential claims, include a compact claim map:

```markdown
| Claim | Status | Source |
|---|---|---|
| ... | implemented/in_progress/planned/deferred/unknown | path:line or artifact id |
```

Use file references when possible (`path:line`) and avoid uncited claims.

## Narrative Arc

- Conception: problem, constraints, why existing options are insufficient.
- Development: key decisions, tradeoffs, architecture, governance.
- Life beyond: roadmap, extension points, contribution pathways.

## Brevity Rules

- Use the fewest words that preserve accuracy.
- Prefer active voice and concrete nouns.
- Keep paragraphs to 1-3 sentences.
- Avoid hype, promises, and metrics without sources.

## Accuracy Checklist

- Only claim features that exist in code or shipped docs.
- Match command titles, IDs, and settings exactly.
- Confirm config paths and defaults.
- Label unknowns as planned or experimental only if sources say so.
- Resolve conflicting sources using the precedence model.
- Mark every forward-looking statement as `planned` or `deferred` with source.

## Status Labels (Required)

Use one of these labels for any feature, workflow, or roadmap claim:

- `implemented` - Exists in current code/docs and is verifiable now.
- `in_progress` - Active work with partial implementation evidence.
- `planned` - Approved/recorded work not yet implemented.
- `deferred` - Explicitly postponed work with a recorded reason.
- `unknown` - Insufficient or conflicting evidence.

Do not use ambiguous labels like "done-ish", "coming soon", or "mostly complete".

## Forward-Looking Guardrails

- Do not present roadmap items as commitments unless explicitly stated in source artifacts.
- Tie all future claims to a concrete source (`BACKLOG`, roadmap entry, plan file, or ticket).
- If no source exists, remove the claim or mark as `unknown`.

## Output Patterns

- README: value statement, quick links, architecture or core systems, beta notice.
- Extension listing: problem, solution, features, commands, configuration, privacy, requirements.
- Release notes: Added, Changed, Fixed with scoped, factual bullets.

