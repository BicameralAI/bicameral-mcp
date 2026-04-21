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

### 0. Boundary detection (pre-ingest, v0.4.16+)

**Trigger** — before extracting any decisions, check whether the input is oversize. Any of the following signals means you must segment the document before ingesting:

- Raw content exceeds ~2000 tokens
- Markdown document contains ≥ 3 H1 headings or ≥ 5 H2 headings
- Transcript contains ≥ 5 distinct speaker turns with ≥ 30s gaps between clusters
- Your first-pass read identifies ≥ 3 distinct topical themes

**If none of these trigger**, skip to step 1 — single-shot ingest stays the common case.

**If oversize**, run the boundary-detection flow:

1. **Use structural signals first**. For markdown PRDs, split on H1/H2 headings. For transcripts, use speaker-turn gaps and timestamp clusters. For Slack exports, use thread boundaries. Only fall back to free-form semantic clustering when no structural signals exist.

2. **Build a segmentation preview** — one entry per proposed topic block:
   ```
   Topic N:
     title: <short title, 3–6 words>
     summary: <one line, what the segment is about>
     source_range: <line range, page range, or timestamp range>
     est_decisions: <integer, ~how many decisions you expect this segment to yield>
   ```

3. **Present the preview to the user VERBATIM** as a numbered list, with every topic visible (title + 1-line summary + source range + estimated decision count). End with: *"Confirm, edit (merge / rename / skip), or re-split?"*

4. **Wait for the user's response**. Accept natural-language edits:
   - "merge 3 and 4" → combine topics 3 and 4 into one block
   - "skip 5" → drop topic 5 from the plan
   - "rename 1 to X" → update title
   - "re-split with 8 topics instead of 5" → re-run segmentation with a finer granularity
   - "confirm" (or equivalent) → proceed to ingest
   
   If the user made any structural edit, re-present the updated preview and wait again. Loop until the user confirms.

5. **Fan out**: after confirmation, call `bicameral.ingest` **once per topic block**. Pass that topic's `title` as the `query` field. Derive each block's decisions from only its own source range. Each call goes through its own brief auto-chain + gap-judge attach.

6. **Roll up at the end**: after all ingests complete, present a single aggregate summary — total decisions ingested, total drifts flagged, total divergences, total gap-judgment findings — followed by per-topic highlights (the 1–2 most actionable findings per topic). Do NOT replay every brief; the user already saw the plan.

**Anti-patterns — reject these**:
- Silently auto-splitting without showing the preview
- Firing N ingests back-to-back without the roll-up (user drowns in N separate briefs)
- Using semantic clustering as the first move when structural signals exist (wastes tokens)
- Fabricating topic titles or decision estimates you aren't confident in — if uncertain, mark as `?` in the preview and let the user decide

### 1. Extract candidate decisions

Read the source. For each statement, decide whether it's a real implementation decision **tied to a business outcome** or whether it should be excluded. Apply the hard-exclude rules first, then the business-tie filter, then the include rules. When in doubt, exclude.

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

**BUSINESS-TIE FILTER (v0.4.19+) — only track implementation decisions tied to a business decision**. Engineering-only decisions and security-only decisions are out of scope unless they're explicitly driven by a business decision (compliance deadline, customer contract, pricing change, UX commitment, revenue target, SLA promise, regulated-data handling).

A decision is **business-tied** when at least one of these is true in the same source:
- A stakeholder-observable outcome is named (user sees X, metric Y moves, compliance check passes, customer contract clause honored)
- A named business driver is present (compliance audit, customer commitment, pricing/packaging, onboarding, churn, growth, revenue, legal/regulatory deadline)
- The decision implements a product/policy decision taken elsewhere in the same source

A decision is **not business-tied** when the entire motivation is engineering hygiene, security hardening, performance optimization, refactor cleanup, test structure, dependency management, CI/CD improvement, or internal developer ergonomics — with no business driver named.

**Reject these (engineering-only / security-only, no business driver)**:

| Category | Example phrase |
|---|---|
| Security hardening with no business driver | "add CSRF tokens to all forms" / "patch the XSS in the search page" / "rotate the JWT signing key" |
| Dependency / supply chain | "bump Django to 5.2" / "replace deprecated crypto lib" |
| Internal refactor | "extract the retry logic into a shared module" / "clean up the duplicate adapter code" |
| Performance without a business SLA | "cache this query" / "add an index to speed up the admin dashboard" |
| Test / CI hygiene | "add unit tests for parser" / "fix the flaky deploy job" / "split the monolith test file" |
| Retry / backoff / timeout mechanics | "retry with exponential backoff" / "bump the SMTP timeout to 10s" |
| Observability tooling | "add Prometheus counters for hit/miss" / "emit a structured log line" |
| Infrastructure ergonomics | "move the rate limiter from in-memory to Redis" (unless driven by a customer scale commitment) |

**Keep these (engineering-shaped but business-tied)**:

| Example | Why it qualifies |
|---|---|
| "Refactor auth middleware to JWTs — Lena flagged in SOC2 review, needed before June audit" | Compliance audit driver |
| "Cap checkout retries at 3 — Stripe reviewer flagged duplicate-charge risk in the contract" | Customer contract driver |
| "Add PII redaction before logging — required by the GDPR assessment" | Regulatory driver |
| "Migrate sessions to Redis before Black Friday — product committed to 20k concurrent checkout" | Business SLA / scale commitment |
| "Cache pricing calls for 5 min — product wants sub-200ms PDP load as a conversion target" | Named business metric driver |

The test: strip the technical verb from the decision. What's left should either (a) name a stakeholder-observable outcome, or (b) cite a named business driver from the same source. If neither, the decision is engineering-only — reject it.

**INCLUDE — concrete decisions with explicit team commitment AND a business tie**:

- Architectural choices, API contracts, data-model decisions, technology choices (with business driver)
- Behavioral requirements with clear definition-of-done (user-observable or compliance-observable)
- Configuration values and refinements that encode a business rule ("set discount tier TTL to 24h", "key on user ID hash per GDPR pseudonymization")
- Action items with code implications, a named owner, AND a business driver

When in doubt, **exclude**. A clean ledger with 5 business-tied decisions is more useful than 20 mixed with engineering hygiene the PM can't act on.

### Worked examples

These cover the failure modes the skill must handle. Read them carefully — they are the spec.

**Example 1 — Strategic / hedged / negated meeting (extract NOTHING)**

> Q3 planning. Priya: "We should probably look into vector embeddings for search someday." Tomás: "If infra approves we'll switch to ScyllaDB for analytics." Lena: "We're keeping the existing webhook retry logic for now." Jin: "We're definitely not going to use Redis here." Tomás: "Eventually I'd love to migrate off the monolith. Maybe 2027."

→ **Extract: 0 decisions, 0 action items.** Every line is hedged, aspirational, status-quo, or negated. The "we're not going to use Redis" line is a non-decision and must NOT be extracted as a "use Redis" decision.

**Example 2 — Mostly business meeting with one buried real decision**

> Q2 OR review. 40 lines about OKR percentages, headcount, customer escalations, fundraising. Buried at line 28: "Oh, by the way, Priya's going to refactor the auth middleware to use JWTs instead of session cookies — Lena flagged it in the SOC2 review and we need it landed before the audit window closes in June." Then back to OKRs.

→ **Extract: 1 decision** — "Refactor the auth middleware to use JWTs instead of session cookies (motivated by SOC2 audit, deadline before June audit window)." Plus 1 action item to Priya. Do NOT extract OKR percentages, headcount, escalations, fundraising, or marketing items as decisions.

**Example 3 — Compound sentence that packs N decisions, each business-tied**

> "Per the enterprise contract we're about to sign, we promised 1000 req/min per tenant and a 99.9% uptime SLA. Move the rate limiter from in-memory to Redis with a 1000-requests-per-minute cap keyed on tenant ID, and cap refund requests at 10/min per tenant since Finance wants to stop the fraud spike we saw last quarter."

→ **Extract: 3 separate decisions**, each tied to a named business driver —
(1) Move rate limiter to Redis (driver: enterprise uptime SLA commitment);
(2) 1000 req/min cap keyed on tenant ID (driver: enterprise contract);
(3) Refund cap at 10/min/tenant (driver: Finance fraud-mitigation ask).
Keep the business driver attached to each decision's description so the gap judge can evaluate it later.

**Example 4 — Same-shape compound sentence, NO business driver (extract NOTHING)**

> "We should move the rate limiter from in-memory to Redis, add Prometheus counters for hits and misses, switch the lease TTL from 60 seconds to 300 seconds, and emit a structured log line on every reject — it's cleaner."

→ **Extract: 0 decisions.** Every clause is engineering hygiene — no stakeholder-observable outcome, no named business driver. "It's cleaner" is the whole motivation. The business-tie filter rejects the entire compound sentence. If the team later tags these as required for a customer commitment, they can be re-ingested then.

**Example 5 — Security hardening: only the business-tied one passes**

> "Priya: let's rotate the JWT signing key quarterly — just good hygiene. Lena: separately, we need to add PII redaction to the audit log before the GDPR self-assessment next month, otherwise we fail the data-minimization check."

→ **Extract: 1 decision** — "Add PII redaction to the audit log (driver: GDPR self-assessment data-minimization check, next month deadline)." The key-rotation line is security hygiene with no business driver named — reject it. A PM reviewing the ledger can act on the GDPR item; they can't act on key rotation.

### 2. Validate relevance against the codebase

For each candidate decision, use the code locator tools to check whether it touches real code:

- Call `search_code` with a query derived from the decision text. If results come back with relevant hits, the decision is groundable.
- If the decision mentions specific symbols (functions, classes, modules), call `validate_symbols` with those names to confirm they exist.
- If a decision returns **zero relevant code hits** and names **no valid symbols**, it is likely strategic — drop it unless it describes something that *should* be built but doesn't exist yet (a genuine "pending" decision).

This step is a lightweight filter, not an exhaustive audit. Spend ~1 search per candidate decision.

### 3. Ingest the filtered set

Call `bicameral.ingest` with a `payload` using the **natural format** (preferred). Only include decisions that passed the relevance filter from step 2.

**Natural format** — canonical fields (use this shape):

```
payload: {
  query: "<topic / feature area — drives the auto-brief>",
  source: "transcript",                      # or "notion", "slack", "document", "manual"
  title: "<source identifier, e.g. sprint-14-planning>",
  date: "2026-04-15",                         # ISO date the meeting / doc happened
  participants: ["Ian", "Brian"],             # optional
  decisions: [
    {
      description: "Cache user sessions in Redis for horizontal scaling",
      id: "sprint-14-planning#session-cache"  # optional stable id
    },
    {
      description: "Apply 10% discount on orders ≥ $100"
    }
  ],
  action_items: [
    { action: "Write retry tests for checkout webhook", owner: "Ian" }
  ]
}
```

**Field rules** — get these right or decisions evaporate:

- **`decisions[].description`** is the canonical text field. `title` is accepted as a synonym for back-compat; `text` is tolerated as an alias (v0.4.16+). At least one of the three must be non-empty or the decision is silently dropped.
- **`action_items[].action`** is the canonical text field. `text` is tolerated as an alias (v0.4.16+). `owner` defaults to `"unassigned"`. `due` is an optional ISO date.
- **`query`** is load-bearing: it's the topic the post-ingest auto-brief and gap-judge chain fire on. If you omit it, the handler falls through to the longest decision description as a topic guess — usable but less focused. **When fanning out from the boundary-detection flow (step 0), always pass each segment's title as `query`.**
- **`participants`** on the payload populates `span.speakers` for every decision. Put the meeting attendees here, not on individual decisions.
- Do NOT include `open_questions` unless they have direct implementation implications — they're accepted as `list[str]` but clutter the ledger with non-code entries.

**Internal format** — only if you already have pre-resolved code regions from `search_code` / `validate_symbols`:

```
payload: {
  query: "...",
  mappings: [
    {
      intent: "Cache user sessions in Redis",
      span: {
        text: "<source excerpt>",
        source_type: "transcript",
        source_ref: "sprint-14-planning",
        meeting_date: "2026-04-15"
      },
      symbols: ["SessionCache"],
      code_regions: [
        { file_path: "src/lib/session.ts", symbol: "SessionCache",
          start_line: 42, end_line: 89, type: "class" }
      ]
    }
  ]
}
```

Use the natural format in the common case. Fall through to internal format only when you already have verified file/line pins — otherwise you'll bypass auto-grounding and the server can't map decisions to code on its own.

### 3b. Verify grounding candidates (v0.4.21+)

When the ingest response contains `sync_status.pending_compliance_checks`
(a non-empty list), the server is asking you to verify whether each
candidate code region actually implements its decision. **This is how
decisions earn REFLECTED status — without your verdict, they stay PENDING.**

For each `PendingComplianceCheck` in the list:

1. **Read the code** at `file_path` lines `start_line`–`end_line` (the
   `code_body` field contains a preview, but read the actual file for
   full context if the snippet is truncated).

2. **Compare** the code against `intent_description`. Ask yourself:
   does this code **functionally implement** the decision, or does it
   just share keywords? A `PaymentProviderService` class that handles
   payment authorization IS a match for "add timeout to payment provider
   authorize calls". A `Payment` model that merely defines a data type
   is NOT.

3. **Write your verdict** by calling `bicameral.resolve_compliance`:
   ```
   bicameral.resolve_compliance({
     phase: "<from the pending check>",
     verdicts: [
       {
         intent_id: "<from check>",
         region_id: "<from check>",
         content_hash: "<from check — MUST echo this back>",
         compliant: true/false,
         confidence: "high"/"medium"/"low",
         explanation: "<1 sentence: why this code does/doesn't implement the decision>"
       }
     ]
   })
   ```

**Batch all verdicts into one `resolve_compliance` call** — the tool
accepts an array. This is a single round-trip, not N calls.

**The `content_hash` is a compare-and-set guard**: you MUST echo back
the exact `content_hash` from the pending check. If the file changed
between the ingest and your read, the server will reject the verdict
and the region stays PENDING until the next drift sweep.

**Skip this step** when `pending_compliance_checks` is empty (all
regions had cached verdicts from prior runs).

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

### 6. Apply the gap-judge rubric (v0.4.16+)

When the ingest response contains a non-null `judgment_payload`, chain
into the `bicameral-judge-gaps` skill to render the rubric sections.

- The `judgment_payload` is only attached by the ingest → brief auto-chain
  (never by standalone `bicameral.brief` calls). If you see it, it means
  the brief produced at least one decision and the server built a context
  pack for caller-session reasoning.
- **Apply the rubric in your own session**. The server has already
  shipped you the decisions (with source excerpts), the rubric (5
  categories, fixed order), and the `judgment_prompt`. Your job is to
  reason over the pack using your own LLM context and, for the
  `infrastructure_gap` category, use your Glob / Read / Grep tools to
  verify implied infrastructure against the category's `canonical_paths`.
- **Output one section per category, in rubric order**. Each section
  starts with the category's `title` as a header. The body uses the
  category's `output_shape`:
  - `bullet_list` → a plain bulleted list
  - `happy_sad_table` → a two-column table (Happy path specified ↔ Missing sad path)
  - `checklist` → `✓ / ○ / ?` prefixed items
  - `absence_matrix` → a checkbox grid
  - `dependency_radar` → a system-by-system list with ✓ discussed / ○ not discussed
- **Cite everything**. Every bullet / row / checklist item must reference
  either a `source_ref` + `meeting_date` from the payload OR a `file:line`
  from your codebase crawl. An uncited item is a bug in your output.
- **Surface VERBATIM**. Quote `source_excerpt` directly. Never paraphrase
  the rubric prompts, never editorialize, never add "as an AI…" hedges.
- **Honest empty path**. If a category produces no findings for this
  pack, emit exactly this single line under its header: `✓ no gaps found`.
  Do not skip the header — the user needs to see that the category was
  applied and found nothing, which itself is information.

The full rendering contract is in `skills/bicameral-judge-gaps/SKILL.md`.
This step is a delegation pointer.

When `judgment_payload` is `null` (the brief had no decisions, or the
chain failed), skip this step silently.

## Arguments

$ARGUMENTS — the transcript text, file path, or description of what to ingest

## Example

User: "Ingest our sprint planning notes from today"
-> Extract 8 candidate decisions from the transcript
-> search_code for each to validate relevance — 5 touch real code, 3 are strategic
-> Call `bicameral.ingest` with 5 filtered decisions in natural format
-> Report: "8 decisions found, 3 dropped (strategic/market), 5 ingested: 3 mapped to code, 2 ungrounded (rate limiting + webhook retry — not yet implemented)"
