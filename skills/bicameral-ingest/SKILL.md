---
name: bicameral-ingest
description: Ingest decisions into the decision ledger. AUTO-TRIGGER on ANY of these: (1) user pastes or mentions a transcript, meeting notes, Slack thread, PRD, spec, or design doc; (2) user says "we decided", "we agreed", "the plan is", "the requirement is", "track this", "log this", "remember this decision", or describes an outcome from a meeting/conversation; (3) user shares notes even informally — e.g. "in our sync yesterday we decided X"; (4) user answers a gap or open question that was previously surfaced by bicameral. When in doubt, ingest — a false trigger that captures zero decisions is cheaper than missing a real decision.
---

# Bicameral Ingest

> Tuning parameters for this skill are defined in `skills/CONSTANTS.md`.

Ingest **implementation-relevant** decisions from a source document into the decision ledger so they can be tracked against the codebase.

## When to use

- User pastes or references a meeting transcript, PRD, design doc, spec, or Slack thread
- User describes the outcome of a meeting or conversation, even informally
- User says "track this", "log this", "we decided X", "we agreed on Y", "the requirement is Z"
- User answers an open question / gap surfaced by bicameral preflight or history
- User shares notes or describes a product decision, even without a structured document

## Telemetry

> **Guard**: Only call `skill_begin` and `skill_end` if telemetry is enabled. Telemetry is enabled by default; disabled by setting `BICAMERAL_TELEMETRY=0` (or `false`/`off`/`no`). If disabled, skip both calls and omit all `diagnostic` tracking.

**At skill start** (before any other tool calls):
```
bicameral.skill_begin(skill_name="bicameral-ingest", session_id=<uuid4>,
  rationale="<one-liner: why this triggered — e.g. 'user pasted sprint notes and said track this'>")
```

**At skill end** (after all work is complete, including ratification):

> ⚠ **USE THESE EXACT FIELD NAMES.** The dashboard queries on `g2_*` / `g3_*` / `g6_*` prefixes. Substituting natural-language names (`grounded`, `channels_read`, `compliance_resolved`, etc.) silently drops the event from every dashboard panel. Unknown fields are not an error — they just become invisible. Copy the names below verbatim.

```
bicameral.skill_end(skill_name="bicameral-ingest", session_id=<stored_id>,
  errored=<bool>, error_class="<if errored — see enum>",
  source_type_ingest="<transcript|slack|notion|document|manual>",
  diagnostic={
    # skill-level
    decisions_ingested: N,
    # G2 fields — extraction filter
    g2_candidates_evaluated: N,
    g2_dropped_hard_exclude: N,
    g2_dropped_l3: N,
    g2_dropped_gate1: N,
    g2_dropped_gate2: N,
    g2_dropped_implied: N,
    g2_parked_context_pending: N,
    g2_proposed_count: N,
    g2_l1_count: N,
    g2_l2_count: N,
    g2_user_overrode: N,
    # G3 fields — symbol grounding
    g3_decisions_grounded: N,      # NOT "grounded"
    g3_decisions_ungrounded: N,    # NOT "ungrounded"
    # G6 fields — compliance verdicts (only when pending_compliance_checks present)
    g6_compliance_checks_received: N,   # NOT "compliance_resolved"
    g6_verdicts_compliant: N,
    g6_verdicts_drifted: N,
    g6_verdicts_not_relevant: N,
    g6_verdicts_cosmetic_autopass: N,
  })
```

`error_class` values (pass only when `errored=true`): `symbol_not_found`, `collision_unresolved`, `drift_mislabeled`, `low_confidence_verdict`, `ledger_empty`, `grounding_failed`, `user_abort`, `other`.

## Steps

### 0. Boundary detection (pre-ingest, v0.4.16+)

**Trigger** — before extracting any decisions, check whether the input is oversize. Any of the following signals means you must segment the document before ingesting:

- Raw content exceeds ~2000 tokens
- Markdown document contains ≥ 3 H1 headings or ≥ 5 H2 headings
- Transcript contains ≥ 5 distinct speaker turns with long gaps suggesting separate sessions
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

3. **Present the preview to the user VERBATIM** as a numbered list, with every topic visible (title + 1-line summary + source range + estimated decision count). Then call `AskUserQuestion`:
   ```
   AskUserQuestion({
     question: "Review these N topic segments — does this look right?",
     header: "Segments",
     multiSelect: false,
     options: [
       { label: "Confirm — proceed with these segments",
         description: "Ingest all N segments as shown." },
       { label: "Edit segments",
         description: "Use the Other field to describe what to merge, skip, or rename." },
       { label: "Re-split finer",
         description: "Generate more, narrower segments." },
       { label: "Re-split broader",
         description: "Generate fewer, wider segments." }
     ]
   })
   ```

4. **Handle the response**:
   - "Confirm" → proceed to step 5.
   - "Edit segments" + Other text → apply the described edits (merge / skip / rename), re-present the updated preview, loop with another `AskUserQuestion` until confirmed.
   - "Re-split finer/broader" → re-run segmentation at the requested granularity, re-present.

   Loop until the user confirms.

5. **Fan out**: after confirmation, call `bicameral.ingest` **once per topic block**. Pass the segment title as **both `query` and `title`** — `title` becomes the `source_ref` stored on every decision in that block, which is the grouping key for the history dashboard. Derive each block's decisions from only its own source range. Each call goes through its own brief auto-chain + gap-judge attach.

6. **Roll up at the end**: after all ingests complete, present a single aggregate summary — total decisions ingested, total drifts flagged, total divergences, total gap-judgment findings — followed by per-topic highlights (the 1–2 most actionable findings per topic). Do NOT replay every brief; the user already saw the plan.

**Anti-patterns — reject these**:
- Silently auto-splitting without showing the preview
- Firing N ingests back-to-back without the roll-up (user drowns in N separate briefs)
- Using semantic clustering as the first move when structural signals exist (wastes tokens)
- Fabricating topic titles or decision estimates you aren't confident in — if uncertain, mark as `?` in the preview and let the user decide

### 0.5. Pre-ingest context pull (v0.7.3+)

**Before extracting any candidates**, query the ledger for existing decisions in the same feature area. This gives you domain priors that inform the business-tie filter and fork test in Step 1.

**Procedure**:

1. Identify the dominant feature area from the source (same noun phrase you'd use in Step 1.5 — e.g. "Session Identity", "Accountable Live", "Checkout Flow").

2. Call `bicameral.search(query=<feature_area>, top_k=10)`.

3. Read the results and note:
   - **Business drivers already established** in this area (privacy, compliance, contract, SLA). A new candidate whose driver matches an established pattern is more likely to be real — even if the source is silent on the driver.
   - **Forks already resolved** (e.g. "we chose opaque keys over direct IDs"). A new candidate that re-raises a resolved fork needs a supersession check, not a fork test.
   - **Empty results** → first ingest of this area; no priors. The filter runs context-free. Circle-back routing (Step 2 park path) is especially important here — be quicker to park than to ingest.

4. Use these priors when evaluating candidates in Step 1:
   - If ledger context establishes a privacy/compliance pattern and a new candidate is privacy-shaped → treat the ledger context as evidence for the business driver, even if the source is silent. Ingest as `proposed` with a note: `"business driver inferred from ledger: <prior decision description>"`.
   - If no ledger context → apply the filters strictly and park ambiguous cases.

**Skip this step** (proceed directly to Step 1) when the source is a completely new domain with no plausible overlap with existing decisions (e.g. first-ever ingest into an empty ledger). The `bicameral.search` call is cheap — when in doubt, call it.

### 1. Extract candidate decisions

**Default to fewer, higher-quality decisions.** A ledger with 3 business-tied decisions that the team will actually act on is more valuable than 10 mixed-quality entries that erode trust. When the filter is ambiguous, park — don't include.

Read the source. For each statement, decide whether it's a real implementation decision **tied to a business outcome** or whether it should be excluded. Apply the hard-exclude rules first, then the business-tie filter, then the include rules. **When in doubt, park (not exclude silently) — the circle-back mechanism surfaces it for resolution.**

**HARD EXCLUDE — these patterns are NEVER decisions, even if they sound technical**:

| Pattern | Example phrase |
|---|---|
| Negation | "we're NOT going to use Redis" |
| Status quo | "keeping the existing X for now" / "no change" |
| Vibes / no observable behavior | "be more performance-focused going forward" |
| Pure metrics / OKR status | "Q3 OKR is at 78%" / "we're at 84% retention" |
| Pure question with no directional signal | "has anyone looked at Sentry vs PostHog?" (exploration request, not a direction) |
| Reversed within the same source | speaker A proposes X → blocked → team agrees on Y → only Y is the decision, X is not |

**SPECULATIVE PROPOSALS — aspirational, hedged, and exploratory candidates are NOT hard-excluded.** If a statement names a concrete subject (a feature, an architecture, a behavior the product could have), capture it as a `proposed` decision regardless of how tentative the language is. Hedged conditionals ("if infra approves, we'll switch to ScyllaDB"), aspirational statements ("I want to prioritize Google Calendar integration"), exploratory ideas ("we need something like Zoom attendance tracking"), and deferred items ("let's revisit this next quarter") all belong in the ledger — the team ratifies or rejects them there. Bicameral's whole value is serving as the central panel for that judgment; silently dropping speculative items before the team sees them defeats the purpose.

- Write the description as the concrete proposed behavior, not as a hedge: *"Analytics storage migrates to ScyllaDB"* not *"If infra approves, switch to ScyllaDB"*
- These route through level classification and the gate filters exactly like committed decisions
- If they survive level/gate filters, ingest as `proposed` — ratification is where the team says yes or no
- Pure vagueness with no concrete subject still hard-excludes: *"be more data-driven"* has no actionable subject; *"add Zoom attendance tracking"* does

**LEVEL CLASSIFICATION GATE (v0.9.3+) — classify before applying any filter.** After the hard-exclude check, assign every surviving candidate a decision level. The level determines which gates apply.

| Level | What it is | Gate 1 (business tie) | Gate 2 (fork) | Description framing |
|---|---|---|---|---|
| **L1 — Product Commitment** | A user-observable behavior the team commits to delivering | **Skip** — the commitment IS the driver | **Skip** — no competing alternative needed | Product/outcome language: "Users can pause their subscription" |
| **L2 — Approach / Architecture** | How a product commitment is implemented | Required (may be inferred from L1 in same source — see Gate 1 below) | Required | Approach language: "Redis-backed sessions for horizontal scale" |
| **L3 — Implementation Detail** | A specific detail implied by an already-chosen L1+L2 | Drop unless exceptional | Drop unless exceptional | Rarely tracked |

**L1 signal patterns** — a candidate is L1 when it contains commitment language + a named user outcome:
- Modal necessity on a user action: "users can/will/must be able to X"
- Product contract: "the system supports/provides/exposes X"
- Behavioral requirement: "when a user does X, the product does Y"
- Acceptance criteria framing: "the feature is complete when X is observable"

**L1 vs. strategy tiebreaker** — an L1 must name a user-observable behavior, not a roadmap intent. If the statement names a date or milestone but no behavior, it is strategy, not L1. Examples:
- "Users can work offline" → **L1** (behavior named)
- "We will ship offline mode in Q3" → **strategy** (date named, no behavior specified — hard-exclude)
- "When connectivity drops, the app queues writes and syncs on reconnect" → **L1** (behavior named, even if no date)

**L1 does NOT require a named business driver or a fork.** The product decision to commit to a feature capability IS the business decision. If the source is a PRD or feature spec, expect L1 to be the primary output. Do not run Gate 1 or Gate 2 on L1 candidates — only hard-exclude check applies.

**L3 exceptions (rarely track)** — only keep an L3 detail if it encodes a non-obvious constraint that would surprise a future developer and is NOT derivable from L1 + L2. Example: "max key length 36 chars — Zoom SDK hard limit." Even then, describe it in product terms if possible.

**DESCRIPTION GRAMMAR — write for CodeGenome (v0.9.3+).** The description you write is source data for semantic grounding. Each level has a required grammar that maps to a distinct CodeGenome layer.

| Level | Required subject | Required predicate | Quality test | CodeGenome role |
|---|---|---|---|---|
| **L1** | A user role (Members, Users, Admins, Guests) | An observable behavior the user experiences | "Could you write a failing acceptance test for this?" | `claim` record — behavioral assertion; PMs query against this |
| **L2** | A component, layer, or approach | Technical behavior + the purpose it serves | "Would a new engineer know *why* this approach was chosen?" | `resolve_subjects` query text — maps to code symbols via graph |
| **L3** | A specific constraint or limit | The external forcing function (SDK limit, API cap, protocol rule) | "Would this surprise a dev who already read L1 + L2?" | Rarely tracked — no CodeGenome identity record |

**L1 description — correct grammar:**
- ✅ "Members can pause their subscription for up to 90 days"
- ✅ "When connectivity drops, the app queues writes and syncs on reconnect"
- ❌ "We will support subscription pausing" — agent is the team, not the user; this is roadmap intent
- ❌ "The system handles session persistence internally" — not user-observable

**L2 description — correct grammar:**
- ✅ "Redis-backed session store enables horizontal scaling across API replicas"
- ✅ "Sidekiq background jobs for CSV export — keeps response times under 200ms on large datasets"
- ❌ "Use Redis for sessions" — missing rationale; CodeGenome cannot match this to a business outcome
- ❌ "Export runs asynchronously" — missing component name and purpose

**BUSINESS-TIE FILTER — Gate 1, L2 decisions only (v0.4.19+).** Skip for L1. Apply to L2: only track implementation decisions tied to a business decision. Engineering-only decisions are out of scope unless explicitly driven by a business outcome (compliance deadline, customer contract, pricing change, UX commitment, revenue target, SLA promise, regulated-data handling). Business driver may be **inferred from an L1 decision in the same source** — if you're ingesting an L1 that says "Users can export data as CSV" and an L2 says "Export runs as a background job via Sidekiq", the L1 provides the business tie for the L2.


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

**FORK TEST — Gate 2, L2 decisions only.** Skip for L1. Every L2 candidate that passes Gate 1 must also pass this test: *Can you name a plausible alternative that a different team might have reasonably chosen instead?*

Ask: "If we handed this spec to two different senior engineering teams, would one team make a meaningfully different choice here?"

- **YES** → real decision (a fork existed) → continue to include rules
- **NO** → specification (any competent team would implement it the same way) → **EXCLUDE**

Common false positives that pass Gate 1 but fail Gate 2:

| Pattern | Why it fails the fork test |
|---|---|
| API response field list | Any team building this system returns the same essential fields — no fork |
| Implied implementation detail | Given a higher-level decision, this is the only reasonable implementation |
| "We'll include X in the Y" | Specification of what X contains, not a choice between alternatives |
| Naming/format conventions | Token prefix or length spec — once you commit to opaque keys, format is trivial |
| Definition of interface contract | "The response returns A, B, C, D" — this is a spec, not a decision |

**REDUNDANCY PRUNING — exclude decisions implied by others you're already keeping.** After drafting your candidate list, check: does decision B necessarily follow from decision A? If yes, keep A and drop B. The ledger should track the minimum deciding set — the choices that uniquely characterize the architecture. Everything implied by those choices is derivable, not an independent decision.

Example: "Use opaque user keys (zoom_member_keys table) instead of exposing Supabase IDs to Zoom" (A) → "zoom_member_keys table maps user_id to am_-prefixed random key, max 36 chars" (B). B is fully implied by A — it's just the format spec. Drop B.

**DENSITY LIMIT — level-aware.** Topic segments are intentionally narrow (3–6 word titles).
- **L1 from a feature spec/PRD**: up to 8 per segment — feature specs legitimately enumerate multiple product commitments.
- **L1 from a transcript**: ≤ 4 per segment — transcripts rarely commit to this many capabilities in one topic.
- **L2**: ≤ 4 per segment. Finding 5+ L2 decisions in one segment almost always means you're enumerating implementation details.
- **L3**: ≤ 1 per segment, only with explicit justification that it encodes a non-derivable constraint.

If you exceed the L2 limit, apply the fork test and redundancy check aggressively to prune.

**ROUTING TABLE — route each candidate by level and gate result:**

| Level | Gate 1 (business tie) | Gate 2 (fork exists) | Driver visible where? | Route |
|---|---|---|---|---|
| **L1** | — (skipped) | — (skipped) | Inherent in commitment | → ingest as `proposed`. Description in product/outcome language. |
| **L2** | Pass | Pass | In the source itself | → ingest as `proposed` |
| **L2** | Pass | Pass | In L1 of same source | → ingest as `proposed`, note: `"business tie inferred from L1: {L1 description}"` |
| **L2** | Pass | Pass | In ledger context (Step 0.5) | → ingest as `proposed`, note: `"driver inferred from ledger: {prior}"` |
| **L2** | Pass | Pass | Nowhere visible | → **park as `context_pending`** with generated question |
| **L2** | Pass | Fail | — | → **drop** (spec, not a decision) |
| **L2** | Fail | — | — | → **drop** (engineering hygiene) |
| **L3** | — | — | — | → **drop** unless encodes non-derivable constraint (see L3 exceptions above) |

The park path is not a consolation prize — it's the right answer when the filter is uncertain. A parked decision surfaces at the next session start and preflight with a specific question. The user resolves it; the ledger stays clean.

**CODEGENOME ALIGNMENT — this level split is intentional infrastructure.** L1 descriptions become `claim` records (behavioral assertions, PM-readable, no code binding). L2 descriptions become `query_text` for `resolve_subjects` (structural anchors that map to code symbols via `get_neighbors`). L1 decisions never generate `subject_identity` records — an L1 without code bindings is correct, not a grounding gap. Writing descriptions to the grammar above means CodeGenome Phase 1 can ingest existing decisions without reformatting.

**INCLUDE — concrete decisions with explicit team commitment AND a business tie**:

- Architectural choices, API contracts, data-model decisions, technology choices (with business driver)
- Behavioral requirements with clear definition-of-done (user-observable or compliance-observable)
- Configuration values and refinements that encode a business rule ("set discount tier TTL to 24h", "key on user ID hash per GDPR pseudonymization")
- Action items with code implications, a named owner, AND a business driver

When in doubt, **park**. A ledger with 3 high-confidence decisions is worth more than 10 mixed-quality entries that the team stops trusting.

### 1.5 Borderline drop confirmation

> **Guard**: Only run this step if guided mode is enabled (`guided: true` in `.bicameral/config.yaml`). In normal mode, skip silently and set `g2_user_overrode = 0` in the diagnostic.

**Scope**: candidates routed to **drop** by Gate 1 (no business tie) or Gate 2 (no fork) only. Hard-excludes, L3 drops, redundancy-pruned candidates, and `context_pending` parks stay silent — those filters are high-confidence.

**If 0 Gate 1/Gate 2 drops**: skip silently, proceed to Step 2.

**If 1–4 borderline drops**: surface a single `AskUserQuestion` with `multiSelect: true`. No pre-selections — the LLM already judged these as drops; the user opts in to rescue rather than out to exclude.

```python
AskUserQuestion({
  question: "Filtered out N borderline candidate(s) — restore any?",
  header: "Filtered out",
  multiSelect: True,
  options: [
    { label: "<decision description ≤ 10 words>",
      description: "Dropped: <gate1: no business driver> | <gate2: no fork — implied>" },
    ...  # one entry per borderline drop
  ]
})
```

**If > 4 borderline drops**: segment into batches of 4. Call `AskUserQuestion` for each batch in sequence (batch 1: items 1–4, batch 2: items 5–8, etc.) using the same `multiSelect: true, no pre-selections` structure. Label the question to indicate position: `"Filtered out N borderline candidate(s) — restore any? (batch M of K)"`. Collect rescued candidates across all batches before proceeding.

**Handle the response**:
- Checked options → rescue those candidates: add them back to the proposed set and proceed normally.
- No selections / skip → proceed with original drops intact.

**Record `g2_user_overrode`** (count of rescued candidates) in the `skill_end` diagnostic.

### Worked examples

These cover the failure modes the skill must handle. Read them carefully — they are the spec.

**Example 1 — Speculative / hedged / negated meeting (capture speculative proposals)**

> Q3 planning. Priya: "We should probably look into vector embeddings for search someday." Tomás: "If infra approves we'll switch to ScyllaDB for analytics." Lena: "We're keeping the existing webhook retry logic for now." Jin: "We're definitely not going to use Redis here." Tomás: "Eventually I'd love to migrate off the monolith. Maybe 2027."

→ **Extract: 3 speculative proposals.** Aspirational and hedged statements with a concrete subject belong in the ledger for team ratification.

| Candidate | Level | Decision | Route |
|---|---|---|---|
| Vector embeddings for search | L2 | "Search uses vector embeddings" | Gate 1: search quality is a product driver ✓. Gate 2: fork (BM25, Elasticsearch) ✓. → proposed |
| ScyllaDB for analytics | L2 | "Analytics storage migrates to ScyllaDB" | Gate 1: analytics performance ✓. Gate 2: fork (stay with current DB) ✓. → proposed |
| Migrate off the monolith | L2 | "Backend migrates off the monolith" | Gate 1: scalability/dev velocity ✓. Gate 2: fork (modular monolith, BFF, etc.) ✓. → proposed |

**Not extracted**: "keeping the existing webhook retry logic" (status quo, no new commitment), "we're definitely not going to use Redis" (negation — not a positive decision).

**These three surface in the ratification prompt.** The team confirms whether they're real commitments or brainstorming noise. That judgment belongs to the team, not the extraction filter.

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

**Example 6 — Token system spec: fork test drops implied details and pure specs (even when they have business drivers)**

> "We'll issue Video SDK JWTs via a zoom-session-token edge function. The edge function validates membership and session eligibility before signing — only eligible members join sessions. We'll determine role_type from the user's host/admin membership and include it in the token so coaches get recording controls and room-movement permissions. Token response contract: signature, sessionName, sessionKey, userKey, roleType. We're using opaque zoom_user_keys from a zoom_member_keys table instead of exposing Supabase IDs to Zoom — eliminates display-name matching fragility, keeps member identity private from the vendor."

Apply both filters — Gate 1 (business tie) then Gate 2 (fork test):

| Candidate | Gate 1: business driver? | Gate 2: fork exists? | Result |
|---|---|---|---|
| Opaque zoom_member_keys (zoom_member_keys table) instead of exposing Supabase IDs | ✓ Privacy, eliminates matching fragility | ✓ Direct ID exposure was the alternative | **KEEP** |
| JWT issued by edge function, validates eligibility before signing | ✓ Only eligible members join | ✓ No gate (open sessions) or backend service were alternatives | **KEEP** |
| role_type (host/member) in token — coaches get different controls | ✓ Role-based session controls | ✗ Given a token-based auth system, encoding identity is implied — no fork | **DROP (implied by #2)** |
| Response contract: signature, sessionName, sessionKey, userKey, roleType | ✓ Frontend depends on it | ✗ Any team implementing this system returns these fields — it's the spec, not a choice | **DROP (spec, not a decision)** |

→ **Extract: 2 decisions** — the opaque key architecture and the eligibility gate. The role_type encoding and the field list are implied or are pure spec — they add no independent signal to the ledger. Note: "Token response contract" passes Gate 1 (has a business driver) but fails Gate 2 (no fork). Gate 1 alone is not sufficient.

**Example 7 — Feature spec (PRD): L1 product commitments are the primary output**

> Feature: Member Subscription Management
> - Members can pause their subscription for up to 90 days without losing their place in the program
> - Members receive an email confirmation within 5 minutes of pausing or resuming
> - Coaches can see which members are paused and their resume date in the member roster
> - Paused members are excluded from session invites and billing cycles automatically
> - Admins can force-resume a paused member from the admin console

Apply level classification first. This is a feature spec — L1 extraction mode.

| Candidate | Level | Hard exclude? | Gate 1 | Gate 2 | Result |
|---|---|---|---|---|---|
| Members can pause subscription up to 90 days without losing program place | L1 | No — clear commitment | — | — | **KEEP** |
| Email confirmation within 5 min of pause/resume | L1 | No — SLA-like observable | — | — | **KEEP** |
| Coaches see paused members + resume date in roster | L1 | No — observable UX | — | — | **KEEP** |
| Paused members excluded from invites + billing | L1 | No — behavioral requirement | — | — | **KEEP** |
| Admin can force-resume from console | L1 | No — capability commitment | — | — | **KEEP** |

→ **Extract: 5 L1 decisions.** All are product commitments — user-observable behaviors the team has committed to. No Gate 1/Gate 2 needed. Descriptions stay in product language (no implementation details). The 90-day limit and 5-minute SLA are not "specs not decisions" — they are specific product commitments that another team might reasonably have set differently (could be 30 days, could be async confirmation). The density limit for L1 from a feature spec is 8, so 5 is fine.

**Anti-pattern caught**: the old filter would have dropped all 5 as "spec, not a decision" (Gate 2 fail — "any team would implement this"). With level classification, these are correctly routed as L1 product commitments that bypass Gate 2.

### 2. Resolve code regions yourself, then hand explicit pins to the server

**This is where grounding quality is won or lost.** The server performs no
code search — you (the caller LLM) resolve explicit `code_regions` before
ingesting. You have full codebase context and real retrieval tools (Grep,
Read, Glob); the server only has the decision text. Use your advantage.

**Procedure per decision**:

1. **Generate symbol hypotheses** from the decision text. If a decision says
   *"all email dispatch functions filter via a single source-of-truth check,"*
   your hypotheses are `dispatchReminders`, `dispatchInterventions`,
   `dispatchNudge`, `resolveMemberStatus`, `isActiveSubscriber` — not just
   the literal word "dispatch."
2. **Use Grep / Read / Glob** (or equivalent native search) to find candidate
   files and symbols in the repo. Open the real source to confirm what each
   candidate actually does.
3. **Call `validate_symbols`** with your resolved candidates to confirm each
   exists in the server's symbol index and get back file/line spans.
4. **Call `get_neighbors`** on a candidate's symbol_id if you need to
   understand scope — surfaces callers/callees so you can tell whether the
   decision is local to one function or spans a call tree.
5. **Build explicit `code_regions`** — `{file_path, symbol, start_line, end_line, type}` —
   from confirmed candidates. Prefer function-level pins over file-level;
   bind to the tightest region that still covers the decision's surface area.

**Grounding quality: filter out false positives before ingesting**. If a
candidate keyword-matches but doesn't actually implement anything related
to the decision, drop it. Example: a decision about email dispatch should
NOT bind to a React `dispatch` reducer just because the word appears.
Ingesting garbage bindings means every edit to that unrelated file
triggers a drift alarm later — noise that drowns out real signal.

**Skip decisions that don't bind to real code**. If after this procedure the
decision has zero concrete regions AND names no valid symbols, it's either
(a) strategic (drop it) or (b) a genuine "pending" decision for code that
doesn't exist yet. For the pending case, ingest it with empty `code_regions`
— it stays ungrounded until a future ingest or `bicameral.bind` call pins
it to real code.

### 2.5 Post-ingest conflict check (v0.9.3+)

After calling `bicameral.ingest`, check for conflicts against existing decisions using the caller-LLM — no server keyword search involved.

The response includes `created_decisions: [{decision_id, description, decision_level}]` — the exact IDs of every decision just created. Use these IDs (not fuzzy text matching) when calling `bicameral_resolve_collision`.

**Procedure:**

0. **Within-batch parent linking (always run — even on first ingest).** During Step 1 you classified each decision as L1/L2/L3 and identified which L2s belong under which L1s. Now that you have the actual `decision_id`s from `created_decisions`, wire up those relationships:
   - For each L2 (or L3) in `created_decisions` that is a child of an L1 (or L2) **in the same batch**, call:
     ```
     bicameral_resolve_collision(new_id=<child_decision_id>, old_id=<parent_decision_id>, action='link_parent')
     ```
   - Match by description: use your extraction-step knowledge of which L2 belonged under which L1. Cross-reference against `created_decisions[].description` to get the right IDs.
   - No human question needed — this is deterministic from your level-classification work in Step 1.
   - Skip this sub-step only if the entire batch is flat (all L1, no L2/L3).

1. Call `bicameral.history(feature_filter=<title used for this ingest>)`. If the result is empty (new feature area, no prior decisions) or if all history entries appear in `created_decisions` (first ingest for this group), skip steps 2–4 (cross-session conflict check only — step 0 still runs above).

2. Compare each entry in `created_decisions` against the pre-existing decisions in the history response (i.e., decisions whose IDs are **not** in `created_decisions`). For each pair, classify:
   - **Cross-level** (new L2 child of existing L1, or new L3 child of existing L2): call `bicameral_resolve_collision(new_id=<child>, old_id=<parent>, action='link_parent')` automatically — no human question. The lower-level decision is always the child (`new_id`).
   - **Same-level, no conflict**: descriptions cover different behaviors. No call needed.
   - **Same-level conflict**: descriptions appear contradictory (e.g., "90-day pause limit" vs. "30-day pause limit" for the same feature). Surface via `AskUserQuestion` — capped at **3 questions per ingest**.

3. For each genuine same-level conflict, call:
   ```
   AskUserQuestion({
     question: "New decision may conflict with an existing one:\n  NEW: \"<new description>\"\n  OLD: \"<old description>\"\nHow should I handle this?",
     header: "Conflict",
     multiSelect: false,
     options: [
       { label: "Supersede the old decision",
         description: "New decision wins. Old is marked superseded and removed from drift tracking." },
       { label: "Keep both as parallel decisions",
         description: "Both are recorded. Flag as a divergence for the next meeting." }
     ]
   })
   ```
   - "Supersede": call `bicameral_resolve_collision(new_id=<from created_decisions>, old_id=<from history>, action='supersede')`.
   - "Keep both": call `bicameral_resolve_collision(new_id=<from created_decisions>, old_id=<from history>, action='keep_both')`.

4. If more than 3 same-level conflicts are found, ask about the first 3 individually, then present a batch gate for the remainder:
   ```
   AskUserQuestion({
     question: "N more potential conflicts exist that I haven't asked about individually. How should I handle them?",
     header: "Batch conflicts",
     multiSelect: false,
     options: [
       { label: "Keep all as parallel decisions (recommended)",
         description: "Record all as non-superseding. Best if decisions cover different contexts." },
       { label: "Review each one now",
         description: "I'll ask about each conflict individually." },
       { label: "Cancel — let me refine the payload first",
         description: "Abort remaining conflict resolution. Re-ingest when ready." }
     ]
   })
   ```

**Advisory-mode override:** if `BICAMERAL_GUIDED_MODE=0`, log conflicts as informational notes only; do not gate the ingest.

### 3. Ingest the filtered set

Call `bicameral.ingest` using the **internal format** with the `code_regions`
you resolved in step 2. Natural format remains supported for truly abstract
decisions with no resolvable code surface — those stay ungrounded until a
future `bicameral.bind` call pins them.

**Internal format** (the default) — use this when you resolved
`code_regions` in Step 2:

```
payload: {
  query: "<topic / feature area — drives the auto-brief>",
  mappings: [
    {
      intent: "Redis-backed sessions for horizontal scale",
      span: {
        text: "we're moving sessions to Redis so we can scale horizontally — Brian committed to this before Black Friday",
        source_type: "transcript",
        source_ref: "sprint-14-planning",
        meeting_date: "2026-04-15",
        speakers: ["Ian", "Brian"]
      },
      symbols: ["SessionCache", "RedisClient"],
      code_regions: [
        { file_path: "src/lib/session.ts", symbol: "SessionCache",
          start_line: 42, end_line: 89, type: "class" },
        { file_path: "src/lib/redis.ts", symbol: "RedisClient",
          start_line: 1, end_line: 34, type: "class" }
      ]
    }
  ]
}
```

**Natural format** (for genuinely abstract decisions) — use when a
decision has no resolvable code surface:

```
payload: {
  query: "<topic / feature area — drives the auto-brief>",
  source: "transcript",                      # or "notion", "slack", "document", "manual"
  title: "<source identifier, e.g. sprint-14-planning>",
  date: "2026-04-15",                         # ISO date the meeting / doc happened
  participants: ["Ian", "Brian"],             # optional
  decisions: [
    {
      description: "Redis-backed sessions for horizontal scale",
      source_excerpt: "we're moving sessions to Redis so we can scale horizontally — Brian committed to this before Black Friday",
      id: "sprint-14-planning#session-cache"  # optional stable id
    },
    {
      description: "SOC2-compliant session storage before Q3 audit",
      source_excerpt: "Lena: we need this landed before the audit window closes in June"
    }
  ],
  action_items: [
    { action: "Write retry tests for checkout webhook", owner: "Ian" }
  ]
}
```

**Field rules** — get these right or decisions evaporate:

- **`mappings[].code_regions`** is the whole game. When you pass explicit regions, the decision is bound exactly where you said. No server-side guessing, no false positives from vocab mismatch.
- **`decisions[].description`** is the concise decision name — ≤15 words, framed by level. It is what appears as the title in the dashboard.
  - **L1**: Product/outcome language — answer "what did the team commit users can do or experience?" No technical terms.
    - Good: `"Members can pause subscription for up to 90 days"` / `"Coaches see paused members in roster"`
    - Bad: `"subscription.status = 'paused' with resume_at timestamp"` (technical, L3)
  - **L2**: Approach language — answer "what implementation approach was chosen?" Name the approach, not the details.
    - Good: `"Opaque Zoom keys prevent cross-group identity leakage"` / `"Redis rate limiter fulfills enterprise 1k req/min SLA"`
    - Bad: `"zoom_member_keys table uses am_-prefixed opaque zoom_user_key instead of Supabase IDs"` / `"Move rate limiter from in-memory to Redis with 1000-requests-per-minute cap keyed on tenant ID"` (too much detail)
  - `title` is accepted as a synonym; `text` is tolerated as an alias. At least one must be non-empty or the decision is silently dropped.
- **`decisions[].source_excerpt`** (natural format) / **`mappings[].span.text`** (internal format) must carry the **verbatim quote** from the source — what was actually said or written. This is stored separately in the ledger as `input_span.text` and surfaces as the source quote in the dashboard. **Never leave it empty and never copy `description` into it** — if the source is a transcript, quote the speaker directly; if a PRD, quote the relevant sentence. If no clean verbatim quote exists, write a 1-sentence paraphrase of what was said, enclosed in brackets: `"[Paraphrase: team agreed to use Redis to meet the scale commitment]"`.
- **`action_items[].action`** is the canonical text field. `text` is tolerated as an alias (v0.4.16+). `owner` defaults to `"unassigned"`. `due` is an optional ISO date.
- **`query`** and **`title`** are both load-bearing. `query` drives the post-ingest auto-brief and gap-judge chain. `title` becomes the `source_ref` stored on every decision in the batch — it is the grouping key that determines which feature section decisions land under in the history dashboard. **When fanning out from the boundary-detection flow (step 0), always pass each segment's title as both `query` and `title`.** If you omit `title`, decisions fall back to "Uncategorized" on the dashboard.
- **`participants`** (natural format) or **`span.speakers`** (internal format) records the meeting attendees.
- Do NOT include `open_questions` unless they have direct implementation implications — they're accepted as `list[str]` but clutter the ledger with non-code entries.

**When to choose which format**:

- **Internal format, always preferred.** You resolved `code_regions` via Step 2. Ingest with explicit pins. The ledger is a trustworthy drift anchor — editing those pinned files fires real drift alarms; editing unrelated files fires nothing. This is the posture we want for real branches.
- **Natural format, for abstract decisions only.** The decision is genuinely abstract ("ship by Q3," "SOC2-compliant session storage") or points at code that doesn't exist yet. It stays ungrounded in the ledger until a future `bicameral.bind` pins it. Honest empty state beats a false binding.

**Context-pending format (v0.7.3+)** — use when a candidate passes the fork test but has no visible business driver in the source or ledger context. Park it rather than silently dropping or forcing inclusion:

```
payload: {
  query: "<feature area>",
  source: "transcript",
  title: "<source ref>",
  date: "<ISO date>",
  decisions: [
    {
      description: "Opaque Zoom keys prevent cross-group identity leakage",
      source_excerpt: "we're going to use opaque zoom_user_key instead of Supabase IDs to prevent user matching across groups",
      signoff: {
        state: "context_pending",
        context_question: "Is this driven by: (a) a privacy/vendor data-isolation requirement, (b) a compliance audit, (c) a customer contract clause, or (d) engineering hygiene only?",
        parked_at: "<ISO datetime>",
        session_id: "<session id>"
      }
    }
  ]
}
```

**Generating `context_question`** — tailor it to the candidate's technical domain:
- Privacy/identity-shaped → "Is this driven by: (a) a privacy requirement or vendor data-isolation policy, (b) a compliance audit, (c) a customer contract, or (d) engineering hygiene only?"
- Reliability-shaped → "Is this driven by: (a) an uptime SLA or customer commitment, (b) a specific incident post-mortem, (c) a contract clause, or (d) engineering hygiene only?"
- Security-shaped → "Is this driven by: (a) a compliance audit or regulatory deadline, (b) a customer security requirement, (c) an incident, or (d) security hygiene only?"
- Default → "Is there a business reason this was implemented this way rather than the simpler alternative? If yes, briefly name it."

Ingest context-pending decisions in the **same `bicameral.ingest` call** as the `proposed` decisions from the same source — do not fire a separate call. The server routes them by `signoff.state`.

### 3b. Verify grounding candidates (v0.4.21+)

When the ingest response contains `sync_status.pending_compliance_checks`
(a non-empty list), the server is asking you to verify whether each
candidate code region actually implements its decision. **This is how
decisions earn REFLECTED status — without your verdict, they stay PENDING.**

Use the `bicameral-sync` compliance resolution flow: for each check, read
`file_path` (use `code_body` preview; read file directly if truncated),
evaluate whether the code functionally implements `decision_description`
(functional match, not keyword match), then batch all verdicts:

```
bicameral.resolve_compliance(
  phase="<from the pending check>",
  flow_id="<sync_status.flow_id>",
  verdicts=[{
    decision_id:  "<check.decision_id>",
    region_id:    "<check.region_id>",
    content_hash: "<check.content_hash — echo exactly>",
    verdict:      "compliant" | "drifted" | "not_relevant",
    confidence:   "high" | "medium" | "low",
    explanation:  "<one sentence>"
  }, ...]
)
```

Verdicts: `"compliant"` = implements correctly · `"drifted"` = diverged ·
`"not_relevant"` = server retrieval mismatch (server prunes the binding).
Echo `content_hash` exactly — it's a CAS guard. One call for all verdicts.

**Skip** when `pending_compliance_checks` is empty.

### 4. Report results

Show the user:
- How many candidate decisions were extracted vs. how many passed the relevance filter
- How many were ingested as `proposed`, how many parked as `context_pending`, how many dropped
- How many ingested decisions mapped to code vs. are ungrounded
- If decisions were dropped, briefly list what was excluded and why ("Dropped 2: spec not a decision; 1: engineering hygiene")
- If decisions were parked, list them with their `context_question` so the user can answer inline if they choose

**Parked decisions surface prompt** — after reporting, if any decisions were parked, call one `AskUserQuestion` per parked decision (batch up to 4 per call; loop for more). Each question presents the options from the decision's `context_question`:

```
AskUserQuestion({
  question: "⚑ Parked: \"<decision description>\"\n<context_question>",
  header: "Parked decision",
  multiSelect: false,
  options: [
    { label: "<option a label — e.g. privacy requirement>",
      description: "Promotes to proposed with this driver recorded." },
    { label: "<option b label — e.g. compliance audit>",
      description: "Promotes to proposed with this driver recorded." },
    { label: "<option c label — e.g. customer contract>",
      description: "Promotes to proposed with this driver recorded." },
    { label: "Engineering hygiene only — drop it",
      description: "Decision was correctly filtered. Marks as rejected." }
  ]
})
```

Handle the response:
- Options (a)/(b)/(c) naming a business driver → re-ingest as `proposed` with the named driver appended to the description. Call `bicameral.ratify` if the user also confirms.
- "Engineering hygiene only — drop it" → call `bicameral.ratify` with `action="reject"` to record an explicit rejection. Do not ratify.
- User selects "Other" and types "leave for now" / similar → the decision stays `context_pending` and surfaces at the next preflight.

### 5. Present the auto-fired brief (v0.4.8+)

`bicameral.ingest` auto-fires `bicameral.brief` on a topic derived from the
payload and returns the brief inside `IngestResponse.brief`. When `brief`
is non-null, surface **only divergences and drifts** using the visual box
format defined in `skills/gap_visualization/SKILL.md`. All other brief
fields (`decisions`, `gaps`, `suggested_questions`) are **suppressed** —
they are either already visible from the ingest summary or will be handled
more precisely by the gap-judge rubric in step 6.

**Rendering order** — always strict:

1. **`divergences` — ALWAYS FIRST if non-empty.** Render each as a
   `⚡ DIVERGENCE` box (template #4 in `gap_visualization`). Two contradicting
   decisions on the same symbol is the highest-stakes signal — stop and
   resolve before continuing.
2. **`drift_candidates`** — render as the `⚠ DRIFTED` callout (template #5).
   No diagram — just name each drifted decision, cite file:line, and point
   to `bicameral.dashboard` for details. Skip any already in divergences.

**Action hints** — surface `action_hints` from the brief verbatim after
the boxes. Two intensities, controlled by `guided: bool`:
- **Normal mode** (`guided: false`, default) — hints fire with `blocking: false`.
  Mention each hint in one line and continue.
- **Guided mode** (`guided: true`) — hints fire with `blocking: true`.
  **Address each blocking hint before any write operation.**

**Never paraphrase a hint's `message` field** — surface it verbatim.

When `brief` is `null` (no derivable topic or chain failed), skip silently.

### 6. Apply the gap-judge rubric (v0.4.16+)

When the ingest response contains a non-null `judgment_payload`, apply the
`bicameral-judge-gaps` rubric using the visual format from
`skills/gap_visualization/SKILL.md`. Full contract is in
`skills/bicameral-judge-gaps/SKILL.md`.

**Key rendering rules for this flow:**
- Render each ask-gap as its corresponding box template (templates #1–#5).
- **Skip empty categories entirely** — no header, no `✓ no gaps found`.
  The user only sees boxes for actual findings.
- End with the roll-up line: `N actionable gap(s) — M of 5 categories had findings.`
  Omit the roll-up entirely when N = 0.
- Max 3 boxes per category; if more exist, surface the batched gate from
  `bicameral-judge-gaps` for the remainder.

When `judgment_payload` is `null` (no decisions or chain failed), skip silently.

### 7. Ratify proposals (v0.7.0+)

All decisions ingested by `bicameral.ingest` enter as **proposals** (`signoff.state =
'proposed'`). Proposals are drift-exempt — drift tracking does not run against them
until they are ratified. Ratification is the user's explicit sign-off that a decision
is committed, not just discussed.

**Position: LAST.** Surface the ratify prompt only after Steps 4, 5, and 6 are
fully complete — report printed, brief rendered, gap-judge findings shown, parked
decisions resolved. The ratify `AskUserQuestion` must be the last user-facing output
of the ingest flow. Do NOT fire it immediately after `bicameral.ingest` returns.

**Multi-segment ingests (Step 0 fan-out):** fire a single ratify prompt at the
very end of the roll-up (after all segment briefs and gap-judge outputs are shown),
covering all decisions across all segments. Do not ratify per segment.

Use `AskUserQuestion`:

**If N ≤ 4 decisions**: use `multiSelect: true` with one option per decision. All pre-selected (recommended). User unchecks any they want to skip.
```
AskUserQuestion({
  question: "Captured N decisions as proposals — select which to ratify now (drift tracking starts on ratified decisions):",
  header: "Ratify",
  multiSelect: true,
  options: [
    { label: "<decision 1 description>", description: "L1/L2 · <feature group>" },
    { label: "<decision 2 description>", description: "L1/L2 · <feature group>" },
    ...
  ]
})
```

**If N > 4 decisions**: use a single-select shortcut:
```
AskUserQuestion({
  question: "Captured N decisions as proposals — ratify now to start drift tracking:",
  header: "Ratify",
  multiSelect: false,
  options: [
    { label: "Ratify all N (recommended)",
      description: "Drift tracking starts immediately on all N decisions." },
    { label: "Pick which to ratify",
      description: "Use the Other field to specify decision numbers (e.g. '1 3 5')." },
    { label: "Skip — review later",
      description: "All stay as proposals. They'll surface as stale after inactivity." }
  ]
})
```

Handle the response:
- **multiSelect result**: ratify the checked decisions; skip the unchecked ones.
- "Ratify all N" → ratify everything.
- "Pick which" + Other text → parse the numbers, ratify the specified subset.
- "Skip" → skip all.

**For each ratified decision**, call:
```
bicameral.ratify(
  decision_id = "<id from ingest response>",
  signer      = "<first speaker in the source, or git user email as fallback>",
  note        = "",   # optional — leave blank unless user provided context
)
```

Confirm the result:
```
✓ Ratified 3/3 — drift tracking active on these decisions.
  (2 skipped — still proposals, will surface as stale after inactivity)
```

**Never silently skip the ratify step.** If the user says "just ingest, don't ask",
record that and skip — but make the skip explicit ("Skipped ratification — these are
proposals; run `bicameral.ratify` when ready to start drift tracking.").

**Signer resolution order:**
1. First named speaker in the source document's `participants` / `speakers` field
2. Meeting organizer if named in the transcript
3. Git user email (`git config user.email`) as final fallback

## Arguments

$ARGUMENTS — the transcript text, file path, or description of what to ingest

## Example

User: "Ingest our sprint planning notes from today"
-> Extract 8 candidate decisions from the transcript
-> Use Grep + Read + validate_symbols to resolve code regions — 5 touch real code, 3 are strategic
-> Call `bicameral.ingest` with 5 filtered decisions (internal format with explicit code_regions for the 3 grounded ones)
-> Report: "8 decisions found, 3 dropped (strategic/market), 5 ingested: 3 mapped to code, 2 ungrounded (rate limiting + webhook retry — not yet implemented)"
-> Show ratify prompt for all 5, default to all, wait for user response
-> Call `bicameral.ratify` for each confirmed decision
