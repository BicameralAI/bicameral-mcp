---
name: bicameral-ingest
description: Ingest a meeting transcript or PRD into the decision ledger. Use when the user pastes a transcript, shares meeting notes, or wants to track decisions from a document.
---

# Bicameral Ingest

Ingest **implementation-relevant** decisions from a source document into the decision ledger so they can be tracked against the codebase.

## When to use

- User pastes or references a meeting transcript
- User shares a PRD, design doc, or Slack thread
- User says "track these decisions" or "ingest this"

## Steps

### 1. Extract candidate decisions

Read the source. For each statement, decide whether it's a real implementation decision or whether it should be excluded. Apply the hard-exclude rules first, then the include rules. When in doubt, exclude.

**HARD EXCLUDE — these patterns are NEVER decisions, even if they sound technical**:

| Pattern | Example phrase |
|---|---|
| Negation | "we're NOT going to use Redis" |
| Hedged conditional | "if infra approves, we'll switch to X" |
| Aspirational | "we should look into" / "eventually" / "someday" / "would love to" |
| Status quo | "keeping the existing X for now" / "no change" |
| Parked / deferred | "let's revisit next quarter" / "park it" |
| Vibes / no observable behavior | "be more performance-focused going forward" |
| Strategy / hiring / pricing / OKRs / fundraising | "Q3 OKR is at 78%" / "tag SAML in CRM" |
| Reversed within the same source | speaker A proposes X → blocked → team agrees on Y → only Y is the decision, X is not |

**INCLUDE — concrete decisions with explicit team commitment**:

- Architectural choices, API contracts, data-model decisions, technology choices
- Behavioral requirements with clear definition-of-done
- Configuration values and refinements ("set TTL to 300s", "key on user ID hash")
- Action items with code implications and a named owner

### Worked examples

These cover the failure modes the skill must handle. Read them carefully — they are the spec.

**Example 1 — Strategic / hedged / negated meeting (extract NOTHING)**

> Q3 planning. Priya: "We should probably look into vector embeddings for search someday." Tomás: "If infra approves we'll switch to ScyllaDB for analytics." Lena: "We're keeping the existing webhook retry logic for now." Jin: "We're definitely not going to use Redis here." Tomás: "Eventually I'd love to migrate off the monolith. Maybe 2027."

→ **Extract: 0 decisions, 0 action items.** Every line is hedged, aspirational, status-quo, or negated. The "we're not going to use Redis" line is a non-decision and must NOT be extracted as a "use Redis" decision.

**Example 2 — Mostly business meeting with one buried real decision**

> Q2 OR review. 40 lines about OKR percentages, headcount, customer escalations, fundraising. Buried at line 28: "Oh, by the way, Priya's going to refactor the auth middleware to use JWTs instead of session cookies — Lena flagged it in the SOC2 review and we need it landed before the audit window closes in June." Then back to OKRs.

→ **Extract: 1 decision** — "Refactor the auth middleware to use JWTs instead of session cookies (motivated by SOC2 audit, deadline before June audit window)." Plus 1 action item to Priya. Do NOT extract OKR percentages, headcount, escalations, fundraising, or marketing items as decisions.

**Example 3 — Compound sentence that packs N decisions**

> "Move the rate limiter from in-memory to Redis with a 100-requests-per-minute cap keyed on user ID hash, add Prometheus counters for hits and misses, switch the lease TTL from 60 seconds to 300 seconds, and emit a structured log line on every reject."

→ **Extract: 5 separate decisions** — (1) move rate limiter from in-memory to Redis, (2) 100 req/min cap keyed on user ID hash, (3) Prometheus counters for hits/misses, (4) lease TTL 60s→300s, (5) structured log line on every reject. Do not collapse these into one.

**Example 4 — Multi-turn debate with reversed pivots**

> Carlos: "Let's use Redis Streams for the webhook queue." Dana: "Infra blocked Streams last quarter." Carlos: "OK, BullMQ then?" Carlos: "Wait, infra also blocked BullMQ." Wei: "SQS?" Team: "SQS FIFO, message group keyed on merchant ID, 5-minute visibility timeout, 6 max receives, dead-letter queue on overflow."

→ **Extract: ONLY the SQS decisions**, not Redis Streams or BullMQ. Specifically: (1) use SQS FIFO for the webhook queue, (2) message group ID = merchant ID, (3) visibility timeout 5 min, (4) max receives 6, (5) DLQ on overflow. The Redis Streams and BullMQ proposals are reversed pivots and must NOT appear in the output.

**Example 5 — Generic vocabulary that should NOT trigger extraction**

> Sara: "We need a better manager for the order workflow. The customer journey from product page to confirmed purchase has too much friction. We should reduce the friction. Add proper handling for the edge cases in the controller." David: "Which controller?" Sara: "Whichever one is closest to where the user-facing latency happens."

→ **Extract: 0 decisions** for the "manager", "customer journey", "friction", and "controller" lines — these are generic business language with no concrete commitment. Do NOT extract a "use a better manager" decision.

**Example 6 — Concrete enumeration mixed with generic chatter**

> Anya: "Add proper handling for the edge cases in the checkout flow — when a payment webhook is delayed, when stock allocation fails midway, when a coupon is invalidated mid-checkout. Right now the user just sees a loading spinner forever."

→ **Extract: 1 decision** — "Handle three failure modes in checkout completion: payment webhook delay, mid-flow stock allocation failure, mid-checkout coupon invalidation, instead of leaving the user on an indefinite loading spinner." Concrete enumeration counts as specificity even though "edge cases" sounds vague at first.

---

When in doubt, **exclude**. A clean ledger with 5 real decisions is more useful than 20 with 15 ghost decisions. If the source's primary topic is OKRs / hiring / fundraising / sales, it is acceptable to return zero decisions.

### 2. Validate relevance against the codebase

For each candidate decision, use the code locator tools to check whether it touches real code:

- Call `search_code` with a query derived from the decision text. If results come back with relevant hits, the decision is groundable.
- If the decision mentions specific symbols (functions, classes, modules), call `validate_symbols` with those names to confirm they exist.
- If a decision returns **zero relevant code hits** and names **no valid symbols**, it is likely strategic — drop it unless it describes something that *should* be built but doesn't exist yet (a genuine "pending" decision).

This step is a lightweight filter, not an exhaustive audit. Spend ~1 search per candidate decision.

### 3. Ingest the filtered set

Call `bicameral.ingest` with a `payload` using the **natural format** (preferred). Only include decisions that passed the relevance filter from step 2.

**Natural format** (use this):
```
payload: {
  decisions: [{ text: "..." }],
  action_items: [{ text: "...", owner: "..." }]
}
```

Do NOT invent extra fields like `title`, `description`, `id`, or `status` — the handler will silently ignore them and produce 0 intents. Stick to the fields in the tool schema. Do NOT include `open_questions` unless they have direct implementation implications.

**Internal format** (only if natural format fails):
```
payload: {
  mappings: [{ intent: "...", span: { text: "...", source_type: "transcript" } }]
}
```

### 4. Report results

Show the user:
- How many candidate decisions were extracted vs. how many passed the relevance filter
- How many were ingested, how many mapped to code, how many are ungrounded
- If decisions were dropped, briefly list what was excluded and why (e.g., "Dropped 3 strategic/market decisions")

## Arguments

$ARGUMENTS — the transcript text, file path, or description of what to ingest

## Example

User: "Ingest our sprint planning notes from today"
-> Extract 8 candidate decisions from the transcript
-> search_code for each to validate relevance — 5 touch real code, 3 are strategic
-> Call `bicameral.ingest` with 5 filtered decisions in natural format
-> Report: "8 decisions found, 3 dropped (strategic/market), 5 ingested: 3 mapped to code, 2 ungrounded (rate limiting + webhook retry — not yet implemented)"
