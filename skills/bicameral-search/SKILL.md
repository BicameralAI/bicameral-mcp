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

## Tester Mode Contract (v0.4.9+)

When the server runs with `BICAMERAL_TESTER_MODE=1`, the response
includes an `action_hints` list. **Hints with `blocking: true` MUST be
addressed before any write operation** (file edits, commits, PRs,
`bicameral_ingest`). Kinds that can fire on search responses:

- **`review_drift`** — one or more matched decisions are drifted. The
  recorded intent no longer matches the current code. Surface the
  drifted files to the user and confirm the code still reflects intent
  BEFORE making changes near them. Refs list includes the drifted
  `intent_id`s and the file paths they touch.
- **`ground_decision`** — one or more matched decisions have no code
  grounding yet. Before implementing something described by an
  ungrounded decision, confirm with the user what should exist, then
  call `bicameral_ingest` with a refreshed payload to ground them.

When `action_hints` is empty (the default in non-tester mode), proceed
normally. Empty is not an error — it means the server is in regular
mode or none of the matches triggered any hint.

Never paraphrase a hint's `message` field to the user — surface it
verbatim so the tester can observe exactly what the server is
signaling.

## Example

User: "/bicameral:search rate limiting"
→ Call `bicameral.search` with query "rate limiting"
→ "Found 2 decisions: (1) 'Rate limit checkout endpoint' — pending, from Sprint 14 planning. (2) 'API rate limiting uses token bucket' — reflected in middleware/rate_limit.py"
