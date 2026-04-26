---
name: bicameral-ingest
description: Ingest decisions into the decision ledger. AUTO-TRIGGER on ANY of these: (1) user pastes or mentions a transcript, meeting notes, Slack thread, PRD, spec, or design doc; (2) user says "we decided", "we agreed", "the plan is", "the requirement is", "track this", "log this", "remember this decision", or describes an outcome from a meeting/conversation; (3) user shares notes even informally — e.g. "in our sync yesterday we decided X"; (4) user answers a gap or open question that was previously surfaced by bicameral. When in doubt, ingest — a false trigger that captures zero decisions is cheaper than missing a real decision.
---

# Bicameral Ingest

Ingest **implementation-relevant** decisions from a source document into the decision ledger so they can be tracked against the codebase.

## When to use

- User pastes or references a meeting transcript, PRD, design doc, spec, or Slack thread
- User describes the outcome of a meeting or conversation, even informally
- User says "track this", "log this", "we decided X", "we agreed on Y", "the requirement is Z"
- User answers an open question / gap surfaced by bicameral preflight or history
- User shares notes or describes a product decision, even without a structured document

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

### 1.5 Assign a feature group (stop-and-ask v0)

After extracting candidate decisions and before resolving code regions,
assign a **feature group** to each decision. A feature group is a short,
canonical noun phrase (2–4 words, title-case, no verbs): e.g.
`"Google Calendar"`, `"Checkout Flow"`, `"Auth Middleware"`.

**Default rule — same source, same group.** When all decisions in this
ingest come from a single source about a single coherent topic, assign
the **same `feature_group` to every decision**. This is the common case:
a Slack thread about "account status SSOT" produces 4 decisions that all
belong to `"Account Status"`, not one group per decision. Only split into
multiple groups when decisions clearly cover distinct, unrelated features.

**Procedure:**

1. **Name the feature group first** from the source title, query, or
   dominant topic. Derive one candidate group name before examining
   individual decisions. A short noun phrase (2–4 words, title-case):
   `"Account Status"`, `"Email Dispatch"`, `"Checkout Flow"`.

2. **Assign that group to every decision by default.** Only diverge for
   a specific decision if it's clearly about a different, unrelated
   feature — in which case go to step 4.

3. **Prefer existing group names.** If `bicameral.history` was called
   earlier in this session and its result is in context, match against
   existing `HistoryFeature.name` values. Reuse verbatim if an existing
   name shares ≥ 2 significant content words with the proposed group.

4. **Stop-and-ask ONLY for cross-feature decisions.** If a specific
   decision clearly spans or belongs to a different feature than the
   dominant group, surface it before calling `bicameral.ingest`:

   ```
   ⚠ I'm not sure how to categorize this decision:
     "<decision text>"

   Proposed group for the rest: "<dominant group>"

   Options:
     a) Use "<dominant group>" anyway
     b) "<existing group B>" (existing)
     c) "<proposed different group>" (new)
     d) Enter a different group name

   Which feature does this belong to?
   ```

   Wait for the user's response. Do not ask for every decision — only
   the ones that genuinely don't fit the dominant group.

5. **Pass `feature_group`** on each decision in the ingest payload.
   For the internal format, add `feature_group` at the mapping level.
   For the natural format, add it on each decision object:
   ```
   decisions: [
     { "description": "...", "feature_group": "Account Status", ... }
   ]
   ```
   For the internal format:
   ```
   mappings: [
     { "intent": "...", "feature_group": "Account Status", ... }
   ]
   ```

   **Never omit `feature_group`.** An unset `feature_group` causes the
   decision to fall back to `source_ref` grouping — every decision ends
   up in its own feature row in the dashboard. This is the primary cause
   of the "5 decisions, 5 features" problem.

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

### 2.5 Premise gate — supersession detection (stop-and-ask v1)

After calling `bicameral.ingest`, read `IngestResponse.supersession_candidates`.
Each entry is a prior decision whose text overlaps with one of the newly
ingested decisions (top 3 per new decision via ledger keyword search).

**v0.8.0: collision-held status.** When `supersession_candidates` is non-empty
for a mapping, that new decision is held at `status='proposal'` with
`signoff.state='collision_pending'` — it does NOT enter the live decision set
until you resolve the collision via `bicameral.resolve_collision`. This prevents
the graph from silently accumulating contradictory decisions.

Classify each candidate:

- **mechanical** (parallel scope) — the prior decision covers a
  different code area, team, or lifecycle phase than the new one.
  Call `bicameral.resolve_collision(new_id=..., old_id=..., action='keep_both')`.
  Both decisions become live proposals. No supersedes edge written.
- **ask** (true supersession) — the prior decision appears to make a
  contradictory or superseded claim about the same behavior. Emit
  ONE question per ask-candidate, capped at **3 questions per ingest**.
  On confirmation, call `bicameral.resolve_collision(new_id=..., old_id=..., action='supersede')`.
  The old decision becomes `status='superseded'`; the new one enters normal flow.

If the queue of ask-candidates exceeds 3, emit the first 3 as
individual questions, then present a batched final approval gate for
the remainder:

```
Bicameral flagged N more potential supersessions that I didn't ask
individually. Options:
  A. Proceed — record all as parallel decisions (treat as non-superseding)
  B. Review them now — list them all and you pick for each
  C. Cancel this ingest — let me refine the payload first
RECOMMENDATION: Choose A if these are decisions from different product
areas; B if any appear to change a commitment the team has already
shipped against.
```

**Session-drop recovery**: if the session ends before `bicameral.resolve_collision`
is called, the new decision remains `status='proposal'` (collision_pending) in the
ledger. Bicameral preflight surfaces it as an "unresolved collision" at the next
session. Resolve it then, or call `bicameral.reset` scoped to that decision to discard it.

**Advisory-mode override:** if `BICAMERAL_GUIDED_MODE=0`, present
supersession candidates as informational notes only; do not gate
the ingest.

### 2.6 Context-for proposals (stop-and-ask v1b)

After calling `bicameral.ingest`, also read `IngestResponse.context_for_candidates`.
Each entry is a `context_pending` decision (one that needs business context before
ratification) whose description keyword-matches the newly ingested span text.

For each candidate, present:
```
This excerpt may answer the open question for decision [decision_description]:
  "<span excerpt>"

Does this span provide the context needed for that decision? [Y/n]
```

On **confirm** (`confirmed=true`): call
`bicameral.resolve_collision(span_id=..., decision_id=..., confirmed=True)`.
An `input_span → context_for → decision` edge is written with `state='confirmed'`.

On **reject** (`confirmed=false`): call with `confirmed=False`. The edge is
written with `state='rejected'` so this span isn't re-surfaced on future ingests.

The decision does NOT automatically advance — it stays `context_pending` until
`bicameral.ratify` is called explicitly (typically after the next preflight surfaces
it as "ready for ratification").

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
      intent: "Cache user sessions in Redis for horizontal scaling",
      span: {
        text: "<source excerpt>",
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
      description: "Cache user sessions in Redis for horizontal scaling",
      id: "sprint-14-planning#session-cache"  # optional stable id
    },
    {
      description: "Ship SOC2-compliant session storage by Q3"
    }
  ],
  action_items: [
    { action: "Write retry tests for checkout webhook", owner: "Ian" }
  ]
}
```

**Field rules** — get these right or decisions evaporate:

- **`mappings[].code_regions`** is the whole game. When you pass explicit regions, the decision is bound exactly where you said. No server-side guessing, no false positives from vocab mismatch.
- **`decisions[].description`** is the canonical text field. `title` is accepted as a synonym for back-compat; `text` is tolerated as an alias (v0.4.16+). At least one of the three must be non-empty or the decision is silently dropped.
- **`action_items[].action`** is the canonical text field. `text` is tolerated as an alias (v0.4.16+). `owner` defaults to `"unassigned"`. `due` is an optional ISO date.
- **`query`** is load-bearing: it's the topic the post-ingest auto-brief and gap-judge chain fire on. If you omit it, the handler falls through to the longest decision description as a topic guess — usable but less focused. **When fanning out from the boundary-detection flow (step 0), always pass each segment's title as `query`.**
- **`participants`** (natural format) or **`span.speakers`** (internal format) records the meeting attendees.
- Do NOT include `open_questions` unless they have direct implementation implications — they're accepted as `list[str]` but clutter the ledger with non-code entries.

**When to choose which format**:

- **Internal format, always preferred.** You resolved `code_regions` via Step 2. Ingest with explicit pins. The ledger is a trustworthy drift anchor — editing those pinned files fires real drift alarms; editing unrelated files fires nothing. This is the posture we want for real branches.
- **Natural format, for abstract decisions only.** The decision is genuinely abstract ("ship by Q3," "SOC2-compliant session storage") or points at code that doesn't exist yet. It stays ungrounded in the ledger until a future `bicameral.bind` pins it. Honest empty state beats a false binding.

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
payload and returns the brief inside `IngestResponse.brief`. When `brief`
is non-null, present it immediately after the ingest summary using the
presentation contract below.

**Presentation order** — always strict, skip empty buckets silently:

1. **`divergences` — ALWAYS FIRST if non-empty.** Two contradictory
   decisions on the same symbol is the highest-stakes signal the brief
   can carry. The meeting's first agenda item should be picking which one
   wins. Surface each divergence as a bold warning with the symbol, file,
   and summary line.
2. **`drift_candidates`** — decisions whose code diverged from recorded
   intent. Present each with status badge (`⚠ DRIFTED`), file:line, and
   drift evidence.
3. **`decisions`** — the full set of in-scope decisions, grouped by status.
   Skip any that already appear in `drift_candidates` to avoid duplication.
4. **`gaps`** — open questions and ungrounded decisions. Present as a
   bulleted list.
5. **`suggested_questions`** — **Surface these VERBATIM**, never paraphrase.
   They're templated to be neutral-voice; paraphrasing reintroduces the
   "me vs you" framing the tool exists to remove.

**Action hints** — the brief response includes `action_hints`. Two intensities,
controlled by `guided: bool` in `.bicameral/config.yaml` or the
`BICAMERAL_GUIDED_MODE=1` env override:

- **Normal mode** (`guided: false`, default) — hints fire with `blocking: false`
  and advisory tone. Mention the hint in one line and continue.
- **Guided mode** (`guided: true`) — hints fire with `blocking: true` and
  imperative tone. **Address each blocking hint before any write operation**
  (file edit, commit, PR, `bicameral_ingest`).

Hint kinds that can fire on brief responses:
- **`resolve_divergence`** — two non-superseded decisions contradict on the
  same symbol. Highest-stakes signal.
- **`review_drift`** — one or more decisions in scope have drifted.
- **`answer_open_questions`** — gap extraction found open-question-shaped gaps.

**Never paraphrase a hint's `message` field** — surface it verbatim.

When `brief` is `null` (the payload had no derivable topic or the chained
brief call failed), skip this step silently. The ingest summary from step 4
is sufficient on its own.

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
-> Use Grep + Read + validate_symbols to resolve code regions — 5 touch real code, 3 are strategic
-> Call `bicameral.ingest` with 5 filtered decisions (internal format with explicit code_regions for the 3 grounded ones)
-> Report: "8 decisions found, 3 dropped (strategic/market), 5 ingested: 3 mapped to code, 2 ungrounded (rate limiting + webhook retry — not yet implemented)"
