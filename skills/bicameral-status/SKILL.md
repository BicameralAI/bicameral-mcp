---
name: bicameral-status
description: Show implementation status of all tracked decisions. Use for sprint reviews, retrospectives, or checking what's been built vs what was decided.
---

# Bicameral Status

Dashboard view — show the implementation status of all tracked decisions in the ledger.

## When to use

- Sprint review / retrospective
- "What decisions are still pending?"
- "Show me the full decision status"
- "What's drifted since last week?"

## Steps

1. Call the `bicameral.status` MCP tool with:
   - `filter` — "all" by default, or "drifted"/"pending"/"reflected"/"ungrounded" from $ARGUMENTS
   - `since` — ISO date if the user specifies a time range (e.g. "since last sprint")
2. Present a summary:
   - Total decisions and breakdown by status
   - List decisions grouped by status, most actionable first (drifted > pending > ungrounded > reflected)
   - For each: description, source (who/when), and mapped code regions
3. Highlight action items: drifted decisions need review, pending need implementation, ungrounded need code mapping

## Arguments

$ARGUMENTS — optional filter ("drifted", "pending") or time range ("since 2026-03-20")

## Example

User: "/bicameral:status pending"
→ Call `bicameral.status` with filter "pending"
→ "3 pending decisions: (1) 'Rate limit checkout' — from Sprint 14, mapped to checkout/controller.py. (2) 'Audit log for admin' — from PRD v1, mapped to admin/base.py. (3) 'Optimistic locking' — ungrounded, no code match."
