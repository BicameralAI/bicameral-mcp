# ADR-0004: Alpha Review Routing Uses Existing Reasons

**Date:** 2026-06-07  
**Status:** proposed  
**Level:** L1
**Related:** BicameralAI/bicameral-bot ADR-0022

## Problem

MCP reads and writes can both surface governance-relevant claims. A query can
discover an underspecified financial-app decision, and an implementation write
can encode a new constraint through tests or code. Routing every such claim
through rich domain roles would add taxonomy and administration before alpha has
proved the core value: reducing EM cognitive debt without creating new
authority confusion.

## Decision

Alpha review routing stays within the product owner/member model. The product
owner is the implicit reviewer for product-scoped Decision review; members may
contribute evidence or request review according to workspace policy. MCP may
produce `DecisionCandidate`, `BindingEvidence`, `ReviewCommand`, or advisory
observations, but it must not introduce built-in domain-role routing fields such
as `payments_owner`, `risk_owner`, `compliance_owner`, or
`suggested_review_context`.

This ownership boundary is defined by `bicameral-bot` ADR-0022. MCP consumes
that authority structure; it does not define product ownership semantics.

When a candidate or command needs review, MCP and governance responses should
use the existing `reason` fields deliberately. The reason should be a short,
source-grounded explanation of the evidence gap or decision rationale, not a
reviewer-assignment layer.

Existing schema fields such as `required_reviewers` or `assigned_reviewers`
remain compatibility plumbing for older governance paths, but alpha product UX
must not use them as per-candidate reviewer routing. The reviewer is derived
from the current product/workspace owner boundary. A later hosted or premium
configuration may support multiple products, each with its own owner/member set.

Example:

```json
{
  "verdict": "needs_review",
  "reason": "ADR covers successful card captures, but no reviewed source specifies reversal timing for failed authorizations."
}
```

## Consequences

Alpha reduces context reconstruction for EMs without making Bicameral own the
customer's org chart or route every candidate to a bespoke reviewer list. Rich
multi-product ownership and role routing remain hosted or premium product-line
candidates, where ownership metadata, codeowners, Jira components, Slack
groups, or compliance workflows can justify the added configuration surface.
