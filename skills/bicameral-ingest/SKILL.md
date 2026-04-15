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

Parse the source content and extract decisions that could plausibly affect code. For each candidate, ask: **"Would this change or constrain an implementation?"**

**Include** (implementation-relevant):
- Architectural choices ("use token bucket for rate limiting")
- API contracts ("the endpoint returns paginated results")
- Data model decisions ("store session tokens encrypted at rest")
- Technology choices ("use tree-sitter for parsing, not regex")
- Behavioral requirements ("retry failed webhooks 3 times with exponential backoff")
- Action items with code implications ("add input validation to the checkout flow")

**Exclude** (not implementation-relevant):
- Business strategy ("target 40-100 design partners at $50K ACV")
- Market positioning ("differentiate from Copilot via upstream decisions")
- Open-ended research questions ("what prevents hallucination beyond grounding?")
- Pricing/packaging decisions with no code impact
- Team/process decisions ("freeze merges Thursday for release cut")
- Vague aspirations without concrete technical implications

When in doubt, **exclude**. A clean ledger with 5 grounded decisions is more useful than 20 with 15 perpetually ungrounded.

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

### 5. Present the auto-fired brief (v0.4.8+)

`bicameral.ingest` auto-fires `bicameral.brief` on a topic derived from the
payload and returns the brief inside ``IngestResponse.brief``. **When
``brief`` is non-null, present it immediately after the ingest summary
using the bicameral-brief presentation rules.** In particular:

- **Lead with divergences** (`brief.divergences`) whenever non-empty. The
  fresh ingest may have just introduced a decision that contradicts an
  existing one on the same symbol — that's the single highest-stakes
  signal bicameral can carry, and the whole reason the brief auto-fires
  after ingest. Surface each divergence as a bold warning with the
  symbol, file path, and summary line.
- Then `brief.drift_candidates`, then `brief.decisions` (grouped by status,
  skipping duplicates that already appear in drift_candidates), then
  `brief.gaps`, then `brief.suggested_questions` **verbatim**.
- Skip any bucket that's empty. If every bucket is empty, say so plainly —
  it means the fresh ingest didn't touch any prior decisions and no
  divergences exist. That itself is useful information.
- **Never** paraphrase `suggested_questions`. They're templated to be
  neutral-voice; paraphrasing reintroduces the "me vs you" framing the
  tool exists to remove.

The full presentation contract lives in `skills/bicameral-brief/SKILL.md`
and is the canonical reference — this step just cross-links it.

When `brief` is `null` (e.g. the payload had no derivable topic or the
chained brief call failed), skip this step silently. The ingest summary
from step 4 is sufficient on its own.

## Arguments

$ARGUMENTS — the transcript text, file path, or description of what to ingest

## Example

User: "Ingest our sprint planning notes from today"
-> Extract 8 candidate decisions from the transcript
-> search_code for each to validate relevance — 5 touch real code, 3 are strategic
-> Call `bicameral.ingest` with 5 filtered decisions in natural format
-> Report: "8 decisions found, 3 dropped (strategic/market), 5 ingested: 3 mapped to code, 2 ungrounded (rate limiting + webhook retry — not yet implemented)"
