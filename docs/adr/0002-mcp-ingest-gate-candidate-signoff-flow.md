# ADR-0002: MCP Ingest Gate Candidate and Signoff Flow

**Date:** 2026-05-27
**Status:** proposed

## Context

Bicameral MCP is the local agent tool surface. During an interactive agent session, the MCP can extract possible decisions from explicit session context and ask the user what to do with them.

The daemon owns shared governance policy, actor permission checks, deduplication, event-store adapters, and durable materialization. The daemon ADRs define substrate-neutral command semantics, but the MCP-specific user interaction belongs here because it is a product decision about the local MCP tool flow.

The important domain distinction is:

- `DecisionCandidate` is an extracted claim that is not yet a Decision.
- `accept_candidate` materializes a Decision.
- `reject_candidate` records review/audit history without creating a Decision by default.
- `approve_signoff` is a separate ownership lifecycle transition.
- `reject_signoff` rejects an existing proposed Decision and keeps it inspectable.

Bicameral governance should not bake in PM/EM role distinctions. It should use owner/member roles plus workspace policy.

## Decision

`bicameral.ingest` presents one MCP user gate over extracted candidates.

For each candidate, the MCP asks the user to accept or reject. The single UX gate may emit multiple domain commands:

- If the user rejects a candidate, MCP submits `reject_candidate`.
- If the user accepts a candidate, MCP submits `accept_candidate`.
- If the accepting actor is allowed by workspace policy to approve signoff, MCP also submits `approve_signoff` as a second applied command.
- If the actor is not allowed to approve signoff, the accepted Decision remains `signoff.state = proposed`.

The MCP must not hide the distinction between accepting a candidate and approving signoff. The daemon response must expose `applied_commands`, candidate/decision identifiers, resulting signoff state, and any `GovernanceResult` warnings or blocks.

Example outcomes:

```json
{
  "candidate_id": "cand_123",
  "decision_id": "D-42",
  "applied_commands": ["accept_candidate"],
  "signoff": { "state": "proposed" }
}
```

```json
{
  "candidate_id": "cand_123",
  "decision_id": "D-42",
  "applied_commands": ["accept_candidate", "approve_signoff"],
  "signoff": { "state": "approved" }
}
```

```json
{
  "candidate_id": "cand_123",
  "applied_commands": ["reject_candidate"],
  "decision_id": null
}
```

## Consequences

Positive:

- Keeps the MCP ingest experience low-friction: one user gate per candidate.
- Keeps event replay explicit: one UX action can still produce multiple domain commands.
- Preserves the distinction between candidate acceptance and signoff approval.
- Lets workspace policy decide whether an owner/member can approve signoff during ingest.
- Avoids creating Decision Ledger records for rejected extraction noise.

Tradeoffs:

- MCP responses must show applied commands clearly, or users will not know whether they accepted a candidate as proposed or accepted and approved it.
- The daemon must validate all actor permissions; MCP cannot assume local user intent equals authorization.
- Tests need to cover both outcomes for accepted candidates: proposed-only and approved.

## Rejected Alternatives

- **Make `bicameral.ingest` only create candidates:** rejected for MCP because it adds too much friction to the interactive agent workflow.
- **Make `accept_candidate` imply signoff approval:** rejected because candidate validity and ownership approval are separate lifecycle transitions.
- **Create rejected Decisions for every rejected candidate:** rejected because extraction garbage should not pollute the Decision Ledger by default.
- **Use PM/EM-specific routing:** rejected because Bicameral governance uses owner/member plus workspace policy rather than explicit PM/EM role distinctions.
