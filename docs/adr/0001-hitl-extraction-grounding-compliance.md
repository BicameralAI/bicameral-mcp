# ADR-0001: HITL Boundaries for Extraction, Grounding, and Compliance

**Date:** 2026-05-27
**Status:** proposed
**Level:** L1
**Related:** BicameralAI/bicameral-daemon `docs/adr/0004-hitl-boundaries-for-probabilistic-governance.md`

## Context

The MCP is the agent's local tool surface. It is where probabilistic judgments
first happen: the caller LLM extracts decisions from messy human context, binds
those decisions to code, and writes compliance verdicts after inspecting the
implementation.

Those are three different uncertainty domains:

1. **Decision extraction** — does the source contain a real implementation
   decision, and did we phrase it correctly?
2. **Code grounding** — is this decision bound to the intended file/symbol/span?
3. **Compliance verdict** — does the inspected code reflect, drift from, or not
   relate to that decision?

Incorrect extraction or grounding can add more cognitive debt than it removes.
A bad decision in the ledger makes future agents over-trust the wrong context; a
bad binding makes preflight, drift, and dashboard surfaces look authoritative
while pointing at the wrong code.

## Decision

The MCP must treat extraction, grounding, and compliance as separate reviewable
claims. Confidence may route work, but it must not silently promote a claim
across boundaries.

### Local workflow states

The local workflow should preserve these states explicitly:

- `proposed` — extracted decision awaits product ratification.
- `context_pending` — extraction could be meaningful but lacks enough business
  or source context to safely enter the ledger as a decision.
- `ungrounded` — decision is meaningful but has no trustworthy code binding.
- `pending` — code binding exists, but compliance has not been resolved.
- `reflected` / `drifted` / `not_relevant` — compliance verdict after code
  inspection.

These states are preferable to false precision. "Unknown" is a valid product
state when the alternative is a plausible-but-wrong binding or verdict.

### HITL boundaries

1. **Product HITL at extraction:** candidate decisions remain proposed until a
   product owner ratifies/rejects them. Agents may draft, deduplicate, and park;
   humans decide meaning.
2. **Developer/EM HITL at grounding:** agents must read candidate code and
   validate symbols before binding. If evidence is weak, leave the decision
   ungrounded and surface it as needing grounding review.
3. **Developer/EM HITL at compliance:** compliance verdicts require inspectable
   evidence from the bound region. Low-confidence verdicts should route to
   review instead of becoming blocking governance.

### Bind-time precision rule

The scoped grounding defect is the moment a `binds_to` edge is created. The MCP
must not rely on later regrounding, drift detection, or dashboard review to
repair a wrong initial edge.

Before calling `bicameral_bind`, the caller must:

1. inspect at least one candidate file;
2. confirm the intended symbol via the symbol index (`validate_symbols`);
3. prefer no binding over a weak binding;
4. let the handler reject unresolved symbols and span mismatches.

Lifecycle regrounding after moves/refactors is adjacent but separate work.

### Compliance confidence rule

A compliance verdict is only as trustworthy as the binding it evaluates. If the
binding is unresolved, ambiguous, or only name-similar, compliance must remain
unknown or review-needed rather than reflected/drifted.

## Consequences

Positive:

- Reduces false-confidence debt in preflight and dashboard surfaces.
- Keeps PM authority over decision meaning and developer/EM authority over code
  evidence.
- Aligns local MCP behavior with daemon-side HITL routing and CI gate semantics.
- Makes incorrect extraction, incorrect bindings, and reviewer edits measurable
  as first-class quality signals.

Tradeoffs:

- More parked/ungrounded/pending states in the short term.
- Fewer automatic hard blocks until evidence quality improves.
- Agents must spend more effort on candidate-code inspection before binding.

## Rejected Alternatives

- **Auto-ratify high-confidence extracted decisions:** rejected because product
  meaning is the canonical human boundary.
- **Bind by semantic/name similarity alone:** rejected because it creates wrong
  downstream compliance with an authoritative-looking status.
- **Collapse extraction, binding, and compliance into one score:** rejected
  because each can fail independently.
