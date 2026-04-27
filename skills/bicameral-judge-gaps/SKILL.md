---
name: bicameral-judge-gaps
description: Apply the v0.4.19 business-requirement gap-judgment rubric to a context pack from bicameral_judge_gaps. Fired automatically when an ingest response carries a judgment_payload. Scope is business requirement gaps ONLY — product, policy, and commitment holes. Engineering gaps (wire protocols, migrations, Dockerfiles, CI, retries) are out of scope and explicitly rejected. Caller-session LLM — the server never reasoned about these gaps, you do.
---

# Bicameral Judge-Gaps

> Tuning parameters for this skill are defined in `skills/CONSTANTS.md`.

This is the **caller-session LLM** half of the v0.4.19 gap judge. The
server (`handlers/gap_judge.py`) built a structured context pack —
decisions in scope, source excerpts, cross-symbol related decision
ids, phrasing-based gaps, a 5-category rubric, and a judgment prompt
— and handed it to you. Your job is to apply the rubric in your own
session and render the findings.

**Server contract**: no LLM was called on the server side. The rubric
and judgment_prompt are static. All reasoning happens here.

**Scope (v0.4.19)**: this rubric surfaces **business requirement
gaps** only — product, policy, and commitment holes a PM, founder,
compliance reviewer, or procurement lead would need to resolve before
engineering can ship with confidence. Engineering gaps (wire
protocols, migration scripts, Dockerfile content, CI pipelines,
retries, race conditions, schema indices) are **out of scope** and
explicitly rejected in each category's prompt. A finding that's
technically correct but engineering-focused is a bug in this rubric.
No codebase crawl is required — reason over `source_excerpt` only.

## When to use

This skill is **not fired directly by user phrasings**. It is a
**chained skill**, invoked in one of two ways:

1. **Auto-chain from `bicameral-ingest`** — when an ingest response
   carries a non-null `judgment_payload`, the ingest skill delegates
   the rubric-rendering to this skill (see step 6 of
   `skills/bicameral-ingest/SKILL.md`).
2. **Explicit call to `bicameral.judge_gaps(topic)`** — when the user
   asks to judge gaps on a specific topic standalone. The tool returns
   a `GapJudgmentPayload` (or `null` on the honest empty path).

If you see a `judgment_payload` in any response envelope, apply this
skill.

## Input contract

You receive a `GapJudgmentPayload` with:

- `topic` — the topic this pack was built for
- `as_of` — ISO datetime, matches the chained brief's `as_of`
- `decisions[]` — one `GapJudgmentContextDecision` per match, each with:
  - `decision_id`, `description`, `status`
  - `source_excerpt`, `source_ref`, `meeting_date` (from v0.4.14)
  - `related_decision_ids` — decision_ids of other decisions on the same symbol
- `phrasing_gaps[]` — pre-existing gaps caught by the deterministic
  `_extract_gaps` pass (tbd markers, open questions, ungrounded). Use
  these as pre-cited evidence when they're relevant to a rubric category.
- `rubric.categories[]` — the 5 categories, in fixed order
- `judgment_prompt` — reinforcement of the rules below

## The 5 rubric categories (fixed order, all business-only)

1. **`missing_acceptance_criteria`** (`bullet_list`)
   For each decision, ask: does the `source_excerpt` define a
   testable **business** outcome for "done"? A business outcome is
   observable by a stakeholder — a user sees X, a metric moves to Y,
   a compliance check passes. Implementation milestones (code lands,
   tests pass, deploy succeeds) are NOT acceptance criteria — ignore
   them. If missing, list the specific acceptance questions the room
   still needs to answer. Quote `source_excerpt` verbatim.

2. **`underdefined_edge_cases`** (`happy_sad_table`)
   For each decision, identify the happy path (what IS specified)
   and the sad path holes from a **business/product** standpoint:
   user-state boundaries (free vs paid, anonymous vs logged-in,
   first-time vs returning), policy exceptions (refunds, overrides,
   escalations), tier boundaries, lifecycle events (churn,
   reactivation, account close). Do **NOT** surface technical
   failure modes (retries, timeouts, network errors, SMTP failures,
   race conditions) — those are engineering concerns. Render:
   | Happy path (specified) | Missing sad path (business edge deferred) |

3. **`infrastructure_gap`** (`checklist`) — **reframed in v0.4.19**
   For each decision, ask whether the implementation implicitly
   commits the business to infrastructure that the team hasn't
   discussed. Business commitments hidden in infra choices include:
   - New SaaS dependency → cost center, procurement, renewal risk
   - Specific cloud vendor / region → vendor lock-in, data portability
   - Data residency jurisdiction → legal / compliance review
   - Implicit SLA (uptime, latency, throughput) → did product commit
     externally?
   - Scale assumption (traffic, storage growth, concurrent users) →
     did product validate the numbers?
   Do **NOT** surface technical hygiene gaps (missing Dockerfile,
   missing CI job, missing env var) — those are engineering. Only
   surface items a PM, CFO, or legal reviewer would need to approve.
   Render a checklist:
   - `○ Decision implies <business commitment> → not discussed / no sign-off`
   Quote the `source_excerpt` phrase that implied the commitment.

4. **`underspecified_integration`** (`dependency_radar`)
   For each decision, extract the external **providers** it implies
   a business relationship with — payment processor, email/SMS
   provider, analytics, CRM, support platform, auth provider, etc.
   Focus on the **business choice** (which vendor, what contract
   tier, what data-sharing scope), NOT the wire protocol / auth
   scheme / API version (engineering details, out of scope).
   Compare against providers explicitly named in related decisions.
   Render:
   - `✓ Provider A → named in decision <decision_id>`
   - `○ Provider B → implied but never named (which vendor?)`
   - `○ Category C → implied but provider category never discussed`
   Never invent a provider the decision didn't name or clearly imply.

5. **`missing_data_requirements`** (`checklist`)
   For each decision, ask whether it implies handling personal /
   regulated / sensitive data without a stated **policy**. Policy
   gaps include:
   - PII / PHI fields collected → classification / consent
     documented?
   - Retention duration → how long is it kept; what triggers
     deletion?
   - User consent / opt-in → captured at what moment; revocable how?
   - Audit trail / access logging → who can see what is logged?
   - Cross-border data flow → residency / GDPR / CCPA review?
   Do **NOT** surface schema mechanics (migration scripts, column
   types, index choices) — those are engineering. Only surface items
   a legal, privacy, or compliance reviewer would flag. Render:
   - `○ Decision implies <policy area> → not addressed`
   Quote the exact `source_excerpt` phrase that implied the data
   concern.

## Ambiguity gate (stop-and-ask v1)

<!-- Copy of bicameral-ask-contract.md v1 — see source for canonical version -->

Before emitting rubric output for a category, classify each gap as
**mechanical** or **ask**:

- **mechanical** — the gap has one obvious resolution the team would
  agree on without discussion (e.g., a retention period where law
  mandates a fixed value; a vendor choice already named in a related
  decision). Note it inline with `✓ resolved: <one line>` and move on.
  Do NOT surface it as an open finding.
- **ask** — reasonable people could disagree or the team has not yet
  addressed this (e.g., which email provider to sign a contract with;
  whether data stays in-region). Emit the finding in the rubric output.

**Per-skill caps (judge-gaps):**
- First min(ask-gaps, 3) surfaced individually in the rubric output
- If ask-gaps > 3: render the first 3 in-rubric, then a batched final
  approval gate at the end:
  ```
  Bicameral flagged N more ambiguous gaps not listed individually.
  A. Proceed — treat all as acknowledged, noted for next planning cycle
  B. Review them now — list all and you decide each
  RECOMMENDATION: Choose A if these are non-blocking; B if any touch
  a near-term compliance or vendor commitment.
  ```

**Advisory-mode override:** if `BICAMERAL_GUIDED_MODE=0`, present all
gaps as informational findings without the batched gate.

## Output contract

**Render ask-gaps as ASCII diagrams, not prose.** The visual templates
are in `skills/gap_visualization/SKILL.md`. Each gap type maps to a
specific diagram style:
- `underdefined_edge_cases` → Railway diagram (happy path `═══` vs
  missing path `─ ─ ─`), or State Machine variant for lifecycle gaps
- `infrastructure_gap` → Dependency Radar (new dependency + affected services)
- `missing_data_requirements` → Data Flow (pipeline with `x x x x` gap markers)
- `missing_acceptance_criteria` and `underspecified_integration` → text only
  (no ASCII diagram; render as a concise bullet list under a header)

**Category ordering** — always strict: acceptance criteria → edge cases →
infra commitments → integration → data policy.

**Skip empty categories entirely** — no header, no `✓ no gaps found`.
Only categories with ask-findings appear in the output.

**Citation rule**: every diagram or bullet must cite `source_ref` +
`meeting_date` from the payload. An uncited item is a bug. Quote
`source_excerpt` verbatim in diagram SOURCE lines — never paraphrase.

**Cap**: max 3 diagrams/bullets per category. If more ask-gaps exist,
surface the batched gate after the third:
```
Bicameral flagged N more ambiguous gaps not listed individually.
A. Proceed — treat all as acknowledged, noted for next planning cycle
B. Review them now — list all and you decide each
RECOMMENDATION: Choose A if these are non-blocking; B if any touch
a near-term compliance or vendor commitment.
```

**Roll-up line** — end the whole section with:
```
N actionable gap(s) — M of 5 categories had findings.
```
Omit when N = 0.

**Do not add categories** outside the rubric. If you notice something
that doesn't fit any of the 5, put it in a plain-text postscript under
`## Observations outside the rubric` — never as a fake rubric category.

## Anti-patterns — reject these

- Rendering prose bullet lists for gap types that have diagram templates
- Rendering a diagram for a mechanical gap (resolve inline, no diagram)
- Emitting a header or `✓ no gaps found` for empty categories
- Emitting findings without citations
- Reordering rubric categories based on severity
- Editorializing ("this is concerning", "the team should…")
- Using hedges ("might be", "possibly", "it seems")
- Paraphrasing `source_excerpt` instead of quoting it verbatim
- **Surfacing engineering gaps** — retry logic, SMTP failure modes,
  Dockerfile absence, schema migration scripts, wire protocol choice,
  auth scheme, race conditions, index choices. Out of scope; suppress.
- Fabricating service names, states, or flow steps the decision didn't
  name — use `<?>` for genuinely unknown parts
- Crawling the codebase — this rubric cites the payload, not files

## Example output structure

```
2 actionable gap(s) — 2 of 5 categories had findings.

┌─────────────────────────────────────────────────────────────┐
│ Edge Case Gap: no reactivation path after account close     │
├─────────────────────────────────────────────────────────────┤
│ Decision: "Win-back flow for churned users"                 │
│           (brainstorm-2026-04-15 · 2026-04-15)              │
│                                                             │
│  ══════════════════════════════════════ Specified           │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ Not addressed      │
│                                                             │
│  [user churn event]                                         │
│       │                                                     │
│       ├════════▶ ACTIVE → PAST_DUE → CANCELED               │
│       │                                                     │
│       └─ ─ ─ ─▶ CANCELED → <win-back??>                     │
│                     │                                       │
│                     ▼                                       │
│               [not defined]  ← no reactivation path        │
│                                                             │
│ SOURCE: "win-back flow for churned users"                   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Data Policy Gap: consent / opt-in moment                    │
├─────────────────────────────────────────────────────────────┤
│ Decision: "Send onboarding email after first login"         │
│           (brainstorm-2026-04-15 · 2026-04-15)              │
│                                                             │
│  DATA FLOW (implied by decision):                           │
│                                                             │
│  [first login] ──▶ [auth service] ──▶ [store: email addr]   │
│                          │                                  │
│                          │     x x x x x x x x              │
│                          │     (no consent policy defined)  │
│                          ▼                                  │
│                   [email sender]                            │
│                                                             │
│  POLICY NEEDED:                                             │
│  - consent: at what moment was opt-in captured?             │
│  - retention: how long is the email stored?                 │
│                                                             │
│ ○ no policy stated                                          │
│ SOURCE: "send onboarding email after first login"           │
└─────────────────────────────────────────────────────────────┘
```

## Arguments

This skill receives a `judgment_payload`, not a user prompt. It is
fired reactively when an ingest or `bicameral.judge_gaps` response
contains the payload.
