# ADR-0004: Alpha Review Routing Uses Existing Reasons

**Date:** 2026-06-07  
**Status:** proposed  
**Level:** L1

## Problem

MCP reads and writes can both surface governance-relevant claims. A query can
discover an underspecified financial-app decision, and an implementation write
can encode a new constraint through tests or code. Routing every such claim
through rich domain roles would add taxonomy and administration before alpha has
proved the core value: reducing EM cognitive debt without creating new
authority confusion.

## Decision

Alpha review routing stays within the existing owner/member and configured
approver model. MCP may produce `DecisionCandidate`, `BindingEvidence`,
`ReviewCommand`, or advisory observations, but it must not introduce built-in
domain-role routing fields such as `payments_owner`, `risk_owner`,
`compliance_owner`, or `suggested_review_context`.

When a candidate or command needs review, MCP and governance responses should
use the existing `reason` fields deliberately. The reason should be a short,
source-grounded explanation of the evidence gap or decision rationale, not a new
role assignment layer.

Example:

```json
{
  "verdict": "needs_review",
  "reason": "ADR covers successful card captures, but no reviewed source specifies reversal timing for failed authorizations.",
  "required_reviewers": ["configured-approver-or-member"]
}
```

## Consequences

Alpha reduces context reconstruction for EMs without making Bicameral own the
customer's org chart. Rich role routing remains a hosted or premium product
line candidate, where ownership metadata, codeowners, Jira components, Slack
groups, or compliance workflows can justify the added configuration surface.
