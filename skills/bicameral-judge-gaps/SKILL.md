---
name: bicameral-judge-gaps
description: Apply the v0.4.19 business-requirement gap-judgment rubric to a context pack from bicameral_judge_gaps. Fired automatically when an ingest response carries a judgment_payload. Scope is business requirement gaps ONLY — product, policy, and commitment holes. Engineering gaps (wire protocols, migrations, Dockerfiles, CI, retries) are out of scope and explicitly rejected. Caller-session LLM — the server never reasoned about these gaps, you do.
---

# Bicameral Judge-Gaps

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

- **One section per category, in rubric order.** Each section starts
  with the category `title` as a header (e.g. `### Missing acceptance criteria`).
- **Every bullet / row / checklist item MUST cite** a `source_ref` +
  `meeting_date` from the payload. v0.4.19 dropped all codebase
  citations — this rubric does not use filesystem tools. An uncited
  item is a bug. Do not emit uncited findings.
- **If a category produces no findings**, emit exactly this single
  line under its header: `✓ no gaps found`. Do not skip the header —
  the user needs to see the category was applied.
- **Surface VERBATIM.** Quote `source_excerpt` directly. Never
  paraphrase the rubric prompts. Never editorialize. Never add
  hedges like "as an AI…" or "it seems that…".
- **Do not reorder categories.** Rubric order is load-bearing — the
  user learns to scan in the order `acceptance → edge cases → infra
  commitments → integration → data policy`.
- **Do not add categories** that aren't in the rubric. If you notice
  something interesting that doesn't fit any of the 5, mention it in
  a plain-text postscript under a clearly-labelled `## Observations
  outside the rubric` section — never in a fake rubric category.
- **Start the whole section with a roll-up line**: something like
  *"Gap judgment for `<topic>` — 5 categories, N findings total."*
  Helps the reader know what to expect.

## Anti-patterns — reject these

- Emitting findings without citations
- Reordering rubric categories based on severity
- Editorialising ("this is concerning", "the team should…")
- Using hedges ("might be", "possibly", "it seems")
- Paraphrasing `source_excerpt` instead of quoting it
- **Surfacing engineering gaps** — retry logic, SMTP failure modes,
  Dockerfile absence, schema migration scripts, wire protocol choice,
  auth scheme, race conditions, index choices. These are out of
  scope for this rubric. If you see one, suppress it.
- Fabricating commitments, providers, or policy implications the
  decision did not state or clearly imply
- Skipping a category header because it's empty — always emit the
  header with `✓ no gaps found`
- Crawling the codebase — v0.4.19 removed the filesystem step; every
  finding cites the payload, not files

## Example output structure

```
Gap judgment for `onboarding email flow` — 5 categories, 6 findings total.

### Missing acceptance criteria
- Decision "Send onboarding email after first login" — source_excerpt says
  "mirrors the welcome-email anti-ghost rule" (brainstorm-2026-04-15 ·
  2026-04-15) but does not define a stakeholder-observable success
  condition (open rate, click rate, drop-off threshold, "user returns
  within 48h" — none specified).

### Happy path specified, sad path deferred
| Happy path (specified) | Missing sad path (business edge deferred) |
|---|---|
| "Send onboarding email after first login" (brainstorm-2026-04-15 · 2026-04-15) | What if user signed up via team invite vs self-serve? — user state boundary not addressed |
| same | What if user is on a paid trial vs free tier? — policy exception not addressed |

### Implied infrastructure commitments not signed off
- ○ Decision implies new email-provider SaaS dependency → cost
  center / procurement not discussed
  "Send onboarding email after first login" (brainstorm-2026-04-15 ·
  2026-04-15) assumes an email sending provider exists; neither cost
  tier nor vendor was named.

### Vendor / provider choices not settled
- ○ Category: email / transactional-mail provider → implied but
  provider category never named (SendGrid? Postmark? SES?)
  (brainstorm-2026-04-15 · 2026-04-15)

### Data policy gaps (PII, retention, consent, audit)
- ○ Decision implies capturing "first login" timestamp → retention
  policy not addressed
  "Send onboarding email after first login" (brainstorm-2026-04-15 ·
  2026-04-15) implies storing a login-time signal per user; how long
  it's kept and whether it's deleted on account close is not stated.
- ○ Decision implies sending email to user address → consent /
  opt-in moment not addressed (same source)
```

## Arguments

This skill receives a `judgment_payload`, not a user prompt. It is
fired reactively when an ingest or `bicameral.judge_gaps` response
contains the payload.
