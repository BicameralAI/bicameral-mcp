---
name: bicameral-search
description: Search past decisions before writing code. Use as pre-flight to surface constraints, prior decisions, and context relevant to a feature or task.
---

# Bicameral Search

Pre-flight check before coding — surface past decisions relevant to what you're about to build.

## When to use

- Before starting implementation on a feature
- When the user asks "what was decided about X?"
- When the user says "check for prior decisions" or "pre-flight"

## Steps

1. Call the `bicameral.search` MCP tool with:
   - `query` — natural language description of the feature or area (from user input or $ARGUMENTS)
   - `min_confidence` — 0.3 for broad search, 0.7 for precise matches
2. Present the results clearly:
   - For each matching decision: description, status (reflected/drifted/pending/ungrounded), who decided it, when, and what code it maps to
   - Highlight any **drifted** decisions — these are constraints that may have been violated
   - Highlight any **pending** decisions — these are agreed-upon but not yet implemented
3. If relevant, suggest how the found constraints should inform the user's implementation plan

## Arguments

$ARGUMENTS — the feature, task, or area to search for prior decisions about

## Action Hint Contract (v0.4.10+)

The response always includes an `action_hints` list. Two intensities,
controlled by the `guided` flag in `.bicameral/config.yaml` (chosen at
`bicameral setup` time) or the `BICAMERAL_GUIDED_MODE=1` env var
override:

- **Normal mode** (`guided: false`, default) — hints fire when
  findings exist but with `blocking: false` and an advisory tone
  ("heads up — N decision(s) look drifted"). **Mention the hint to
  the user in your response** (one line is enough) and continue with
  what they asked for. It's a heads-up, not a stop sign.
- **Guided mode** (`guided: true`) — same hints with `blocking: true`
  and imperative tone ("N matched decision(s) have drifted — review
  BEFORE making changes"). **Address each blocking hint before any
  write operation** (file edit, commit, PR, `bicameral_ingest`).

Kinds that can fire on search responses:

- **`review_drift`** — one or more matched decisions are drifted.
  Refs: drifted `intent_id`s + the file paths they touch.
- **`ground_decision`** — one or more matched decisions have no code
  grounding yet. Refs: the ungrounded `intent_id`s.

When `action_hints` is empty, none of the matches triggered any hint
— proceed normally.

**Never paraphrase a hint's `message` field** — surface it verbatim
so the user can observe exactly what the server signaled. The
phrasing intentionally varies by mode so the user can tell at a
glance whether the agent is being advised or required to pause.

For the full guided-mode contract, see
`skills/bicameral-guided/SKILL.md`.

## Example

User: "/bicameral:search rate limiting"
→ Call `bicameral.search` with query "rate limiting"
→ "Found 2 decisions: (1) 'Rate limit checkout endpoint' — pending, from Sprint 14 planning. (2) 'API rate limiting uses token bucket' — reflected in middleware/rate_limit.py"
