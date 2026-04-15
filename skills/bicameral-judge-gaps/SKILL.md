---
name: bicameral-judge-gaps
description: Apply the v0.4.16 gap-judgment rubric to a context pack from bicameral_judge_gaps. Fired automatically when an ingest response carries a judgment_payload. Caller-session LLM — the server never reasoned about these gaps, you do.
---

# Bicameral Judge-Gaps

This is the **caller-session LLM** half of the v0.4.16 gap judge. The
server (`handlers/gap_judge.py`) built a structured context pack —
decisions in scope, source excerpts, cross-symbol related decision
ids, phrasing-based gaps, a 5-category rubric, and a judgment prompt
— and handed it to you. Your job is to apply the rubric in your own
session and render the findings.

**Server contract**: no LLM was called on the server side. The rubric
and judgment_prompt are static. All reasoning happens here.

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
  - `intent_id`, `description`, `status`
  - `source_excerpt`, `source_ref`, `meeting_date` (from v0.4.14)
  - `related_decision_ids` — intent_ids of other decisions on the same symbol
- `phrasing_gaps[]` — pre-existing gaps caught by the deterministic
  `_extract_gaps` pass (tbd markers, open questions, ungrounded). Use
  these as pre-cited evidence when they're relevant to a rubric category.
- `rubric.categories[]` — the 5 categories, in fixed order
- `judgment_prompt` — reinforcement of the rules below

## The 5 rubric categories (fixed order)

1. **`missing_acceptance_criteria`** (`bullet_list`)
   For each decision, ask: does the `source_excerpt` define a testable
   "done" condition? If not, list the specific missing acceptance
   questions the room still needs to answer. Quote `source_excerpt`
   verbatim on citation.

2. **`underdefined_edge_cases`** (`happy_sad_table`)
   For each decision, identify the happy path (what IS specified) and
   the sad path holes (failure modes, boundary conditions, error
   handling deferred or absent). Render as a two-column table:
   | Happy path (specified) | Missing sad path (deferred) |
   Use only evidence in `source_excerpt`. Never invent a failure mode
   the team didn't hint at.

3. **`infrastructure_gap`** (`checklist`, **requires codebase crawl**)
   For each decision, enumerate implied infrastructure: database,
   cache, queue, CDN, env vars, secrets, CI/CD jobs, deploy targets.
   Then use your Glob / Read / Grep tools to check the category's
   `canonical_paths` for each implied item. Render a checklist:
   - `✓ Implied X → found in <file_path>:<line>` (always cite file:line)
   - `○ Implied Y → missing` (not found in any canonical_path)
   - `? Implied Z → ambiguous` (partial match, cite it)
   Never claim a match without citing file:line. Never fabricate
   implied infra the decision didn't imply.

4. **`underspecified_integration`** (`dependency_radar`)
   For each decision, extract external systems/APIs it implies
   touching (name them explicitly from `source_excerpt`). Compare
   against the set of systems discussed in related decisions'
   excerpts. Render as:
   - `✓ System A → discussed in <intent_id>`
   - `○ System B → touched but never discussed`
   Never invent an integration the decision didn't name.

5. **`missing_data_requirements`** (`checklist`)
   For each decision, ask: does it imply schema changes, migrations,
   data retention, or PII handling? If the decision implies any of
   these but neither the `source_excerpt` nor related decisions
   address them, surface as:
   - `○ Decision implies <schema_change> → not addressed`
   Cite the exact phrase in `source_excerpt` that implied the data
   change. Never fabricate a schema implication.

## Output contract

- **One section per category, in rubric order.** Each section starts
  with the category `title` as a header (e.g. `### Missing acceptance criteria`).
- **Every bullet / row / checklist item MUST cite** either:
  - A `source_ref` + `meeting_date` from the payload, OR
  - A `file:line` from your codebase crawl (`infrastructure_gap` only)
  An uncited item is a bug. Do not emit uncited findings.
- **If a category produces no findings**, emit exactly this single
  line under its header: `✓ no gaps found`. Do not skip the header —
  the user needs to see the category was applied.
- **Surface VERBATIM.** Quote `source_excerpt` directly. Never
  paraphrase the rubric prompts. Never editorialize. Never add
  hedges like "as an AI…" or "it seems that…".
- **Do not reorder categories.** Rubric order is load-bearing — the
  user learns to scan in the order `acceptance → edge cases → infra
  → integration → data`.
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
- Fabricating infra, integrations, or schema implications the
  decision did not state or imply
- Skipping a category header because it's empty — always emit the
  header with `✓ no gaps found`

## Example output structure

```
Gap judgment for `onboarding email flow` — 5 categories, 7 findings total.

### Missing acceptance criteria
- Decision "Send onboarding email after first login" — source_excerpt says
  "mirrors the welcome-email anti-ghost rule" (brainstorm-2026-04-15 ·
  2026-04-15) but does not define what makes the email flow "done" —
  no success criterion, no completion test.

### Happy path specified, sad path deferred
| Happy path (specified) | Missing sad path (deferred) |
|---|---|
| "Send onboarding email after first login" (brainstorm-2026-04-15) | What if SMTP send fails? retry / drop / queue — not specified |

### Implied infrastructure not verified
- ○ Implied SMTP/email service → missing
  Crawled .github/workflows/, Dockerfile, docker-compose.yml, terraform/,
  k8s/ — no email service declared.
- ✓ Implied background worker → found in docker-compose.yml:12
  (redis worker is provisioned; email delivery presumably queues here)

### External systems touched but not discussed
- ○ Welcome email service → touched but never discussed
  The related decision "Welcome email after first invoice succeeds"
  (pricing-review · 2026-03-08) references a welcome-email flow but
  neither decision names the provider (SendGrid? Postmark? SES?).

### Data model implications not addressed
- ○ Decision implies tracking "first login" timestamp → not addressed
  "Send onboarding email after first login" (brainstorm-2026-04-15)
  implies a `users.first_login_at` column or event, but neither the
  decision nor related decisions address where this is stored or
  when it's set.
```

## Arguments

This skill receives a `judgment_payload`, not a user prompt. It is
fired reactively when an ingest or `bicameral.judge_gaps` response
contains the payload.
