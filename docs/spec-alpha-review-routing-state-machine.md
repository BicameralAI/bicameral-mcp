# Alpha Review Routing State Machine

This spec defines the alpha routing behavior for MCP reads and writes that
surface governance-relevant observations. It follows ADR-0004: alpha uses the
product owner/member model, and it uses existing `reason` fields to reduce
context reconstruction without adding domain-role or per-candidate reviewer
routing fields.

The product authority structure is defined by `bicameral-bot` ADR-0022. MCP is
the local agent-facing tool surface; it consumes the bot-owned Product Owner /
Workspace Member boundary instead of defining reviewer authority itself.

## Goals

- Preserve the distinction between advisory observations and canonical
  authority.
- Let MCP reads and writes both surface useful `DecisionCandidate` and binding
  observations.
- Route review with existing `reason` and review-state fields.
- Treat the current product owner as the implicit reviewer for alpha.
- Avoid new domain-role taxonomy such as `payments_owner`, `risk_owner`, or
  `compliance_owner` in alpha.

## Decision Candidate Flow

```text
                         +-------------------+
                         |     ToolRequest   |
                         |  query or action  |
                         +---------+---------+
                                   |
                                   v
                         +-------------------+
                         | Classify surface  |
                         | query | action    |
                         | bind  | enforce   |
                         +---------+---------+
                                   |
          +------------------------+-------------------------+
          |                                                  |
          v                                                  v
+---------------------+                         +----------------------+
| Query-shaped read   |                         | Action-shaped write  |
| "Did we decide ACH  |                         | "Update ADR: ACH     |
| reversal timing?"   |                         | limit = $10k/day"    |
+----------+----------+                         +----------+-----------+
           |                                               |
           v                                               v
+----------------------+                        +----------------------+
| Existing authority?  |                        | Explicit persistence |
| Decision already in  |                        | request?             |
| Decision Ledger?     |                        |                      |
+----+------------+----+                        +----+------------+----+
     | yes        | no                               | yes        | no/implicit
     v            v                                  v            v
+---------+  +--------------------+          +----------------+ +------------------+
| Answer  |  | Extract advisory   |          | Draft / update | | Execute work     |
| from    |  | observation from   |          | artifact       | | with observation |
| Decision|  | SourceEvidence     |          |                | | capture enabled  |
+----+----+  +---------+----------+          +-------+--------+ +--------+---------+
     |                 |                             |                   |
     v                 v                             v                   v
+------------+  +---------------------+      +----------------+ +------------------+
| Optional   |  | Potential           |      | DecisionCandidate| Potential         |
| operational|  | DecisionCandidate   |      | from explicit    | DecisionCandidate |
| receipt    |  | "reversal timing    |      | user intent      | "tests define     |
| only       |  | unspecified"        |      |                  | chargeback policy"|
+------------+  +----------+----------+      +--------+-------+ +---------+--------+
                           |                          |                   |
                           v                          v                   v
                  +------------------+       +------------------+ +------------------+
                  | Governance policy|       | Governance policy| | Governance policy|
                  | confidence +     |       | confidence +     | | confidence +     |
                  | owner review     |       | owner review     | | owner review     |
                  +--------+---------+       +--------+---------+ +---------+--------+
                           |                          |                   |
                           v                          v                   v
                  +------------------+       +------------------+ +------------------+
                  | ReviewState      |       | ReviewState      | | ReviewState      |
                  | needs_review     |       | needs_review     | | needs_review or  |
                  | with reason      |       | with reason      | | request_evidence |
                  +--------+---------+       +--------+---------+ +---------+--------+
                           |                          |                   |
                           v                          v                   v
                  +--------------------------------------------------------------+
                  | Review command                                               |
                  | approve -> materialize accepted event through event store     |
                  | reject  -> keep rejected/non-canonical history                |
                  | request_evidence -> preserve candidate, ask for better source |
                  +--------------------------------------------------------------+
```

## Binding And Grounding Flow

```text
+-------------------------------+
| ToolRequest                   |
| "Is enhanced-KYC ACH limit    |
| enforced in this PR?"         |
+---------------+---------------+
                |
                v
+-------------------------------+
| Build grounding request       |
| changed files, symbols,       |
| candidate decision/binding    |
+---------------+---------------+
                |
                v
+-------------------------------+
| Validate local evidence       |
| code locator / ledger facts   |
+---------------+---------------+
                |
                v
+-------------------------------+
| Evidence state                |
+-------+-----------+-----------+
        |           |
        | verified  | weak / missing / ambiguous / stale
        v           v
+----------------+  +-------------------------------+
| BindingEvidence|  | Advisory observation or       |
| may enter      |  | candidate binding hint        |
| governance     |  |                               |
+-------+--------+  +---------------+---------------+
        |                           |
        v                           v
+----------------------+   +-------------------------------+
| Review command       |   | ReviewState:                  |
| bind_to_code or      |   | needs_grounding_review or     |
| resolve_compliance   |   | request_evidence              |
+----------+-----------+   +---------------+---------------+
           |                               |
           v                               v
+----------------------+       +---------------------------+
| product owner reviews|       | reason explains what is   |
| command; members may |       | missing, not which domain |
| contribute evidence  |       | role must review          |
+----------+-----------+       +---------------------------+
           |
           v
+----------------------+
| Accepted governance  |
| event materializes   |
| canonical state      |
+----------------------+
```

## Reason Requirements

`reason` is required when a candidate or command is routed to review because it
is the main alpha mechanism for reducing context reconstruction.

A good reason is:

- one sentence;
- source-grounded;
- about the evidence gap, decision rationale, or command consequence;
- free of implied domain-role assignment.

Good:

```json
{
  "verdict": "needs_review",
  "reason": "ADR covers successful card captures, but no reviewed source specifies reversal timing for failed authorizations."
}
```

Bad:

```json
{
  "verdict": "needs_review",
  "reason": "Compliance owner must review because this violates Reg E.",
  "domain_role": "compliance_owner",
  "required_reviewers": ["compliance-owner"]
}
```

The bad example adds reviewer-routing fields and overstates a compliance
conclusion that alpha has not proven.

## Alpha Routing Rules

| Situation | Alpha behavior |
|---|---|
| Query returns an existing Decision | Answer from the Decision Ledger; optional operational receipt only. |
| Query discovers an evidence gap | Create or surface a non-canonical `DecisionCandidate`; route with `reason` if review is requested. |
| Action explicitly asks to persist a decision | Create or update the relevant artifact; route through governance policy before materialization. |
| Action implicitly encodes a decision through tests or code | Surface a candidate only when the implementation introduces a material constraint; use `reason` to explain the inferred constraint. |
| Binding evidence is verified | Allow binding/review command to enter governance. |
| Binding evidence is weak, missing, stale, or ambiguous | Keep advisory or request evidence; do not materialize binding or compliance authority. |
| Review is required | Route to the current product owner implicitly; members may contribute evidence according to workspace policy. |

## Non-Goals

- Do not add `domain_role`, `suggested_review_context`, or financial-domain
  reviewer fields in alpha.
- Do not use `required_reviewers` or `assigned_reviewers` as alpha product
  semantics. Existing fields may remain for compatibility, but the alpha UX
  should derive review from the current product owner boundary.
- Do not add multi-product owner/member routing in alpha. That belongs in a
  later hosted or premium configuration surface.
- Do not let MCP directly write canonical Decisions, accepted bindings, signoff,
  or compliance state.
- Do not use `reason` to imply verified compliance or legal conclusions that
  governance has not accepted.
